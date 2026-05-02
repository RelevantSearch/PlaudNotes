"""Shared pytest fixtures.

Tests use an in-memory Firestore fake by default (FakeFirestoreClient
below). Set FIRESTORE_INTEGRATION=1 to require the real emulator and
fail rather than fall back to the fake.
"""

from __future__ import annotations

import asyncio
import copy
from typing import Any


class _FakeDocSnapshot:
    def __init__(self, data: dict[str, Any] | None) -> None:
        self._data = data
        self.exists = data is not None

    def to_dict(self) -> dict[str, Any] | None:
        if self._data is None:
            return None
        return copy.deepcopy(self._data)


class _FakeDocRef:
    def __init__(self, store: dict[str, Any], doc_id: str) -> None:
        self._store = store
        self._id = doc_id

    async def get(self) -> _FakeDocSnapshot:
        return _FakeDocSnapshot(self._store.get(self._id))

    async def set(self, data: dict[str, Any]) -> None:
        self._store[self._id] = copy.deepcopy(data)

    async def update(self, data: dict[str, Any]) -> None:
        if self._id not in self._store:
            raise KeyError(self._id)
        self._store[self._id].update(copy.deepcopy(data))

    async def delete(self) -> None:
        self._store.pop(self._id, None)


class _FakeCollection:
    def __init__(self, store: dict[str, Any]) -> None:
        self._store = store

    def document(self, doc_id: str) -> _FakeDocRef:
        return _FakeDocRef(self._store, doc_id)


class FakeFirestoreClient:
    """Async-shaped, in-memory stand-in for google.cloud.firestore_v1.AsyncClient.

    Only implements the surface FirestoreStore exercises: collection(name)
    → document(id) → get/set/update/delete. Good enough for unit tests.
    """

    def __init__(self) -> None:
        self._collections: dict[str, dict[str, Any]] = {}

    def collection(self, name: str) -> _FakeCollection:
        return _FakeCollection(self._collections.setdefault(name, {}))

    async def close(self) -> None:
        return None

    # Helper for assertions
    def raw(self, collection: str, doc_id: str) -> dict[str, Any] | None:
        return self._collections.get(collection, {}).get(doc_id)


# Re-export helper so test modules can construct fakes without importing here.
__all__ = ["FakeFirestoreClient"]


# Make sure pytest-asyncio is alive — silence "asyncio_mode" warning if
# someone overrides pyproject.toml setting.
def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    _ = asyncio
    for item in items:
        if "asyncio" in item.keywords:
            continue
