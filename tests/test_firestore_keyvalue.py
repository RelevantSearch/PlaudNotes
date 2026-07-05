"""Tests for FirestoreKeyValue — the AsyncKeyValue adapter FastMCP uses
for OAuth client + authorization-code storage.
"""

from __future__ import annotations

import asyncio

import pytest

from plaud_notes_mcp.firestore_keyvalue import FirestoreKeyValue
from tests.conftest import FakeFirestoreClient


@pytest.fixture
def kv():
    return FirestoreKeyValue(
        project_id="p", database_id="(default)", firestore_client=FakeFirestoreClient()
    )


@pytest.mark.asyncio
async def test_put_then_get(kv):
    await kv.put("k1", {"a": 1, "b": "two"}, collection="clients")
    got = await kv.get("k1", collection="clients")
    assert got == {"a": 1, "b": "two"}


@pytest.mark.asyncio
async def test_get_unknown_returns_none(kv):
    assert await kv.get("missing", collection="clients") is None


@pytest.mark.asyncio
async def test_default_collection_when_none(kv):
    await kv.put("k", {"x": 1}, collection=None)
    assert await kv.get("k", collection=None) == {"x": 1}


@pytest.mark.asyncio
async def test_collections_are_isolated(kv):
    await kv.put("k", {"v": "a"}, collection="ca")
    await kv.put("k", {"v": "b"}, collection="cb")
    assert (await kv.get("k", collection="ca")) == {"v": "a"}
    assert (await kv.get("k", collection="cb")) == {"v": "b"}


@pytest.mark.asyncio
async def test_delete_returns_true_when_existed(kv):
    await kv.put("k", {"x": 1}, collection="c")
    assert await kv.delete("k", collection="c") is True
    assert await kv.delete("k", collection="c") is False


@pytest.mark.asyncio
async def test_ttl_expiry(kv):
    await kv.put("k", {"x": 1}, collection="c", ttl=0.05)
    val, ttl = await kv.ttl("k", collection="c")
    assert val == {"x": 1}
    assert ttl is not None and ttl > 0
    await asyncio.sleep(0.1)
    assert await kv.get("k", collection="c") is None


@pytest.mark.asyncio
async def test_ttl_none_when_no_ttl_set(kv):
    await kv.put("k", {"x": 1}, collection="c")
    _, ttl = await kv.ttl("k", collection="c")
    assert ttl is None


@pytest.mark.asyncio
async def test_get_many(kv):
    await kv.put("a", {"v": 1}, collection="c")
    await kv.put("b", {"v": 2}, collection="c")
    rows = await kv.get_many(["a", "missing", "b"], collection="c")
    assert rows == [{"v": 1}, None, {"v": 2}]


@pytest.mark.asyncio
async def test_put_many_and_delete_many(kv):
    await kv.put_many(["a", "b", "c"], [{"v": 1}, {"v": 2}, {"v": 3}], collection="c")
    deleted = await kv.delete_many(["a", "missing", "c"], collection="c")
    assert deleted == 2
    rows = await kv.get_many(["a", "b", "c"], collection="c")
    assert rows == [None, {"v": 2}, None]


@pytest.mark.asyncio
async def test_ttl_many(kv):
    await kv.put("a", {"v": 1}, collection="c", ttl=10)
    await kv.put("b", {"v": 2}, collection="c")
    pairs = await kv.ttl_many(["a", "b", "missing"], collection="c")
    assert pairs[0][0] == {"v": 1} and pairs[0][1] is not None
    assert pairs[1] == ({"v": 2}, None)
    assert pairs[2] == (None, None)
