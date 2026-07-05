"""Firestore-backed AsyncKeyValue for FastMCP's OAuth machinery.

FastMCP's GoogleProvider/OAuthProxy expects a `client_storage: AsyncKeyValue`
for OAuth client registrations, authorization codes, and related state.
We provide a Firestore-backed implementation so multi-instance Cloud Run
deployments share state correctly.

Schema: each logical (collection, key) maps to one Firestore document at
path `<collection>/<key>` with fields:
  value:      dict (the stored payload)
  expires_at: timestamp | None (for TTL)
"""

from __future__ import annotations

import datetime as _dt
import logging
import time
from collections.abc import Mapping, Sequence
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_COLLECTION = "_default"


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _coll_name(collection: str | None) -> str:
    return collection or DEFAULT_COLLECTION


class FirestoreKeyValue:
    """AsyncKeyValue protocol implementation backed by Firestore.

    Lazy-initializes the AsyncClient on first call. Test fakes can be
    injected via firestore_client.
    """

    def __init__(
        self,
        *,
        project_id: str,
        database_id: str,
        firestore_client: Any | None = None,
    ) -> None:
        self._project_id = project_id
        self._database_id = database_id
        self._fs = firestore_client

    async def get(self, key: str, *, collection: str | None = None) -> dict[str, Any] | None:
        snap = await self._doc(collection, key).get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        if self._is_expired(data):
            await self._doc(collection, key).delete()
            return None
        return data.get("value")

    async def ttl(
        self, key: str, *, collection: str | None = None
    ) -> tuple[dict[str, Any] | None, float | None]:
        snap = await self._doc(collection, key).get()
        if not snap.exists:
            return None, None
        data = snap.to_dict() or {}
        if self._is_expired(data):
            await self._doc(collection, key).delete()
            return None, None
        return data.get("value"), self._remaining_ttl(data)

    async def put(
        self,
        key: str,
        value: Mapping[str, Any],
        *,
        collection: str | None = None,
        ttl: float | None = None,
    ) -> None:
        record: dict[str, Any] = {"value": dict(value), "expires_at": None}
        if ttl is not None:
            record["expires_at"] = _now() + _dt.timedelta(seconds=float(ttl))
        await self._doc(collection, key).set(record)

    async def delete(self, key: str, *, collection: str | None = None) -> bool:
        ref = self._doc(collection, key)
        snap = await ref.get()
        if not snap.exists:
            return False
        await ref.delete()
        return True

    async def get_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> list[dict[str, Any] | None]:
        return [await self.get(k, collection=collection) for k in keys]

    async def ttl_many(
        self, keys: Sequence[str], *, collection: str | None = None
    ) -> list[tuple[dict[str, Any] | None, float | None]]:
        return [await self.ttl(k, collection=collection) for k in keys]

    async def put_many(
        self,
        keys: Sequence[str],
        values: Sequence[Mapping[str, Any]],
        *,
        collection: str | None = None,
        ttl: float | None = None,
    ) -> None:
        if len(keys) != len(values):
            raise ValueError(f"keys/values length mismatch: {len(keys)} vs {len(values)}")
        for k, v in zip(keys, values):
            await self.put(k, v, collection=collection, ttl=ttl)

    async def delete_many(self, keys: Sequence[str], *, collection: str | None = None) -> int:
        deleted = 0
        for k in keys:
            if await self.delete(k, collection=collection):
                deleted += 1
        return deleted

    def _doc(self, collection: str | None, key: str):
        fs = self._firestore()
        return fs.collection(_coll_name(collection)).document(key)

    def _firestore(self):
        if self._fs is not None:
            return self._fs
        from google.cloud.firestore_v1 import AsyncClient

        kwargs: dict[str, Any] = {"project": self._project_id}
        if self._database_id and self._database_id != "(default)":
            kwargs["database"] = self._database_id
        self._fs = AsyncClient(**kwargs)
        return self._fs

    @staticmethod
    def _is_expired(data: dict[str, Any]) -> bool:
        exp = data.get("expires_at")
        if exp is None:
            return False
        # Convert sub-second-precise comparison: emulator stores as datetime
        if isinstance(exp, _dt.datetime):
            return _now() >= exp
        # Fallback: treat as epoch seconds
        try:
            return time.time() >= float(exp)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _remaining_ttl(data: dict[str, Any]) -> float | None:
        exp = data.get("expires_at")
        if exp is None:
            return None
        if isinstance(exp, _dt.datetime):
            now = _now()
            if now >= exp:
                return None
            return (exp - now).total_seconds()
        try:
            return max(0.0, float(exp) - time.time())
        except (TypeError, ValueError):
            return None
