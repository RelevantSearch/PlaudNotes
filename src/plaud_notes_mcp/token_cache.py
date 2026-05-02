"""In-process LRU cache fronting FirestoreStore for hot-path Plaud-token lookups.

KMS decrypt is ~30-80ms p50, ~250ms p99, with quotas measured per region.
Decrypting on every MCP tool call would be both slow and quota-bound.
This cache holds decrypted StoredToken instances by google_sub for a
short TTL.

Cache hit/miss counts are exposed via metrics() and emitted to Cloud
Logging via structured log records — a Cloud Logging log-based metric
extracts the ratio for monitoring (see plan-1 Phase 6 follow-up).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict

from plaud_notes_mcp.firestore_store import StoredToken

logger = logging.getLogger(__name__)


class TokenCache:
    """LRU cache with TTL, per-sub locks, and hit/miss metrics."""

    def __init__(self, store, ttl_seconds: float = 60.0, max_size: int = 50) -> None:
        self._store = store
        self._ttl = float(ttl_seconds)
        self._max = int(max_size)
        self._cache: OrderedDict[str, tuple[StoredToken, float]] = OrderedDict()
        self._locks: dict[str, asyncio.Lock] = {}
        self._hits = 0
        self._misses = 0
        self._lock_table_lock = asyncio.Lock()

    async def get(self, google_sub: str) -> StoredToken | None:
        cached = self._lookup(google_sub)
        if cached is not None:
            self._hits += 1
            logger.info(
                "token_cache hit", extra={"cache_event": "hit", "google_sub_prefix": google_sub[:8]}
            )
            return cached

        # Cache miss — collapse concurrent fetches via a per-sub lock so the
        # KMS decrypt only runs once even under fan-in.
        lock = await self._get_lock(google_sub)
        async with lock:
            cached = self._lookup(google_sub)
            if cached is not None:
                self._hits += 1
                logger.info(
                    "token_cache hit (collapsed)",
                    extra={"cache_event": "hit", "google_sub_prefix": google_sub[:8]},
                )
                return cached

            self._misses += 1
            logger.info(
                "token_cache miss",
                extra={"cache_event": "miss", "google_sub_prefix": google_sub[:8]},
            )
            row = await self._store.get_user_token(google_sub)
            if row is None:
                return None
            self._put(google_sub, row)
            return row

    def invalidate(self, google_sub: str) -> None:
        self._cache.pop(google_sub, None)

    def metrics(self) -> dict[str, int]:
        return {"hits": self._hits, "misses": self._misses, "size": len(self._cache)}

    def _lookup(self, google_sub: str) -> StoredToken | None:
        entry = self._cache.get(google_sub)
        if entry is None:
            return None
        token, expires_at = entry
        if time.monotonic() >= expires_at:
            self._cache.pop(google_sub, None)
            return None
        # Mark recently used — move to end of LRU.
        self._cache.move_to_end(google_sub)
        return token

    def _put(self, google_sub: str, token: StoredToken) -> None:
        expires_at = time.monotonic() + self._ttl
        self._cache[google_sub] = (token, expires_at)
        self._cache.move_to_end(google_sub)
        while len(self._cache) > self._max:
            self._cache.popitem(last=False)  # evict LRU

    async def _get_lock(self, google_sub: str) -> asyncio.Lock:
        async with self._lock_table_lock:
            lock = self._locks.get(google_sub)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[google_sub] = lock
            return lock
