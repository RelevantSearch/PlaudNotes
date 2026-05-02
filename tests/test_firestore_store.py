"""Tests for the Firestore-backed user-token store with KMS envelope encryption.

Uses an in-memory FakeFirestoreClient (see conftest) and an in-memory KMS
fake. Higher-fidelity emulator-based tests can run in CI by injecting a
real AsyncClient instead of the fake.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from plaud_notes_mcp.firestore_store import (
    FirestoreStore,
    StoredToken,
)
from tests.conftest import FakeFirestoreClient

PROJECT_ID = "test-plaud-mcp"
DATABASE_ID = "(default)"
KMS_KEY = "projects/p/locations/us-central1/keyRings/kr/cryptoKeys/k"


@pytest.fixture
def fake_kms_client():
    """A fake AsyncKMS client where ciphertext = b'CT:' + plaintext.

    Easy to assert in tests; predictable inverse for decrypt.
    """
    client = AsyncMock()

    async def encrypt(request, **_):
        return type(
            "EncryptResp",
            (),
            {
                "ciphertext": b"CT:" + request["plaintext"],
                "ciphertext_crc32c": type("Crc", (), {"value": 0})(),
            },
        )()

    async def decrypt(request, **_):
        ct = request["ciphertext"]
        if not ct.startswith(b"CT:"):
            raise RuntimeError("decrypt: malformed ciphertext")
        return type(
            "DecryptResp",
            (),
            {
                "plaintext": ct[3:],
                "plaintext_crc32c": type("Crc", (), {"value": 0})(),
            },
        )()

    client.encrypt = encrypt
    client.decrypt = decrypt
    return client


@pytest.fixture
async def store(fake_kms_client):
    """Build a FirestoreStore against in-memory fakes."""
    s = FirestoreStore(
        project_id=PROJECT_ID,
        database_id=DATABASE_ID,
        kms_key_name=KMS_KEY,
        firestore_client=FakeFirestoreClient(),
        kms_client=fake_kms_client,
    )
    yield s
    await s.close()


def _sub() -> str:
    return f"sub-{uuid.uuid4().hex[:12]}"


@pytest.mark.asyncio
async def test_encrypt_then_decrypt_roundtrip(store):
    sub = _sub()
    await store.save_user_token(
        google_sub=sub, email="a@example.com", plaud_token="eyJ.AAA.bbb", region="us"
    )
    fetched = await store.get_user_token(sub)
    assert isinstance(fetched, StoredToken)
    assert fetched.plaud_token == "eyJ.AAA.bbb"
    assert fetched.email == "a@example.com"
    assert fetched.region == "us"


@pytest.mark.asyncio
async def test_unknown_sub_returns_none(store):
    assert await store.get_user_token(_sub()) is None


@pytest.mark.asyncio
async def test_kms_failure_during_encrypt_raises(store, fake_kms_client):
    fake_kms_client.encrypt = AsyncMock(side_effect=RuntimeError("kms boom"))
    with pytest.raises(RuntimeError, match="kms boom"):
        await store.save_user_token(
            google_sub=_sub(), email="a@example.com", plaud_token="eyJ.x.y", region="us"
        )


@pytest.mark.asyncio
async def test_save_replaces_ciphertext_preserving_created_at(store):
    sub = _sub()
    await store.save_user_token(
        google_sub=sub, email="a@example.com", plaud_token="eyJ.OLD.zz", region="us"
    )
    first = await store.get_user_token(sub)
    assert first is not None
    initial_created = first.created_at

    await store.save_user_token(
        google_sub=sub, email="a@example.com", plaud_token="eyJ.NEW.zz", region="eu"
    )
    second = await store.get_user_token(sub)
    assert second is not None
    assert second.plaud_token == "eyJ.NEW.zz"
    assert second.region == "eu"
    assert second.created_at == initial_created  # preserved on re-key
    assert second.last_rekeyed_at is not None
    assert second.last_rekeyed_at >= initial_created


@pytest.mark.asyncio
async def test_delete_user_token(store):
    sub = _sub()
    await store.save_user_token(
        google_sub=sub, email="a@example.com", plaud_token="eyJ.x.y", region="us"
    )
    assert await store.get_user_token(sub) is not None
    await store.delete_user_token(sub)
    assert await store.get_user_token(sub) is None


@pytest.mark.asyncio
async def test_get_returns_none_when_kms_decrypt_fails(store, fake_kms_client):
    sub = _sub()
    await store.save_user_token(
        google_sub=sub, email="a@example.com", plaud_token="eyJ.x.y", region="us"
    )
    fake_kms_client.decrypt = AsyncMock(side_effect=RuntimeError("kms decrypt boom"))
    with pytest.raises(RuntimeError, match="kms decrypt boom"):
        await store.get_user_token(sub)
