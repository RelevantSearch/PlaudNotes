"""Tests for the in-process LRU cache that fronts FirestoreStore."""

from __future__ import annotations

import asyncio
import datetime as _dt

import pytest

from plaud_notes_mcp.firestore_store import StoredToken
from plaud_notes_mcp.token_cache import TokenCache


class _FakeStore:
    """Minimal async store that records calls; returns rows from a dict."""

    def __init__(self, rows: dict[str, StoredToken] | None = None) -> None:
        self.rows: dict[str, StoredToken] = dict(rows or {})
        self.calls: list[str] = []
        self.delay_seconds: float = 0.0

    async def get_user_token(self, google_sub: str) -> StoredToken | None:
        self.calls.append(google_sub)
        if self.delay_seconds:
            await asyncio.sleep(self.delay_seconds)
        return self.rows.get(google_sub)


def _row(sub: str, token: str = "eyJ.x.y", region: str = "us") -> StoredToken:
    return StoredToken(
        google_sub=sub,
        email=f"{sub}@example.com",
        plaud_token=token,
        region=region,
        created_at=_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc),
        last_rekeyed_at=None,
    )


@pytest.mark.asyncio
async def test_hit_skips_store_call():
    store = _FakeStore({"sub-a": _row("sub-a")})
    cache = TokenCache(store, ttl_seconds=60, max_size=10)
    first = await cache.get("sub-a")
    second = await cache.get("sub-a")
    assert first is not None and second is not None
    assert first.plaud_token == "eyJ.x.y"
    assert store.calls == ["sub-a"]  # only one underlying read


@pytest.mark.asyncio
async def test_miss_reads_store():
    store = _FakeStore({"sub-a": _row("sub-a")})
    cache = TokenCache(store, ttl_seconds=60, max_size=10)
    assert await cache.get("sub-missing") is None
    assert store.calls == ["sub-missing"]


@pytest.mark.asyncio
async def test_ttl_expiration_forces_refetch():
    store = _FakeStore({"sub-a": _row("sub-a")})
    cache = TokenCache(store, ttl_seconds=0.05, max_size=10)
    await cache.get("sub-a")
    await asyncio.sleep(0.1)
    await cache.get("sub-a")
    assert store.calls == ["sub-a", "sub-a"]


@pytest.mark.asyncio
async def test_lru_eviction_at_capacity():
    rows = {f"sub-{i}": _row(f"sub-{i}") for i in range(3)}
    store = _FakeStore(rows)
    cache = TokenCache(store, ttl_seconds=60, max_size=2)

    await cache.get("sub-0")
    await cache.get("sub-1")
    await cache.get("sub-2")  # evicts sub-0
    await cache.get("sub-0")  # second underlying read for sub-0

    assert store.calls.count("sub-0") == 2
    assert store.calls.count("sub-1") == 1
    assert store.calls.count("sub-2") == 1


@pytest.mark.asyncio
async def test_invalidate_drops_entry():
    store = _FakeStore({"sub-a": _row("sub-a")})
    cache = TokenCache(store, ttl_seconds=60, max_size=10)
    await cache.get("sub-a")
    cache.invalidate("sub-a")
    await cache.get("sub-a")
    assert store.calls == ["sub-a", "sub-a"]


@pytest.mark.asyncio
async def test_concurrent_misses_collapse_to_single_store_call():
    store = _FakeStore({"sub-a": _row("sub-a")})
    store.delay_seconds = 0.05  # simulate slow Firestore + KMS hop
    cache = TokenCache(store, ttl_seconds=60, max_size=10)

    results = await asyncio.gather(*(cache.get("sub-a") for _ in range(5)))
    assert all(r is not None and r.plaud_token == "eyJ.x.y" for r in results)
    assert store.calls == ["sub-a"]  # collapsed via per-sub lock


@pytest.mark.asyncio
async def test_metrics_track_hits_and_misses():
    store = _FakeStore({"sub-a": _row("sub-a")})
    cache = TokenCache(store, ttl_seconds=60, max_size=10)
    await cache.get("sub-a")  # miss
    await cache.get("sub-a")  # hit
    await cache.get("sub-b")  # miss
    metrics = cache.metrics()
    assert metrics["hits"] == 1
    assert metrics["misses"] == 2
