"""Firestore-backed user-token store with KMS envelope encryption.

Owns the `user_tokens/{google_sub}` collection. Plaud bearer tokens are
KMS-encrypted before write; plaintext never lands in Firestore.

OAuth machinery (clients + authorization codes) lives in a separate
FirestoreKeyValue adapter for FastMCP — see Phase 5.
"""

from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StoredToken:
    google_sub: str
    email: str
    plaud_token: str  # plaintext after decrypt; never persisted
    region: str
    created_at: _dt.datetime
    last_rekeyed_at: _dt.datetime | None


class FirestoreStore:
    """Firestore + KMS user-token store. Async-only.

    Construction is cheap; clients are created lazily on first call so
    importing this module never reaches out to GCP.
    """

    USER_TOKENS = "user_tokens"

    def __init__(
        self,
        *,
        project_id: str,
        database_id: str,
        kms_key_name: str,
        firestore_client: Any | None = None,
        kms_client: Any | None = None,
    ) -> None:
        self._project_id = project_id
        self._database_id = database_id
        self._kms_key_name = kms_key_name
        self._fs = firestore_client
        self._kms = kms_client

    async def close(self) -> None:
        # google-cloud-firestore AsyncClient has a close() coroutine.
        fs = self._fs
        if fs is not None and hasattr(fs, "close"):
            try:
                maybe = fs.close()
                if hasattr(maybe, "__await__"):
                    await maybe
            except Exception:  # pragma: no cover — best-effort teardown
                logger.debug("Firestore client close failed", exc_info=True)

    async def save_user_token(
        self,
        *,
        google_sub: str,
        email: str,
        plaud_token: str,
        region: str,
    ) -> None:
        """Encrypt and persist; preserves created_at on re-key."""
        ciphertext = await self._encrypt(plaud_token)
        now = _dt.datetime.now(_dt.timezone.utc)
        doc_ref = (await self._coll(self.USER_TOKENS)).document(google_sub)
        snap = await doc_ref.get()
        if snap.exists:
            update: dict[str, Any] = {
                "email": email,
                "region": region,
                "plaud_token_ciphertext": ciphertext,
                "kms_key_version": self._kms_key_name,
                "last_rekeyed_at": now,
            }
            await doc_ref.update(update)
        else:
            await doc_ref.set(
                {
                    "google_sub": google_sub,
                    "email": email,
                    "region": region,
                    "plaud_token_ciphertext": ciphertext,
                    "kms_key_version": self._kms_key_name,
                    "created_at": now,
                    "last_rekeyed_at": None,
                }
            )

    async def get_user_token(self, google_sub: str) -> StoredToken | None:
        doc_ref = (await self._coll(self.USER_TOKENS)).document(google_sub)
        snap = await doc_ref.get()
        if not snap.exists:
            return None
        data = snap.to_dict() or {}
        plaintext = await self._decrypt(data["plaud_token_ciphertext"])
        return StoredToken(
            google_sub=data["google_sub"],
            email=data["email"],
            plaud_token=plaintext,
            region=data["region"],
            created_at=data["created_at"],
            last_rekeyed_at=data.get("last_rekeyed_at"),
        )

    async def delete_user_token(self, google_sub: str) -> None:
        doc_ref = (await self._coll(self.USER_TOKENS)).document(google_sub)
        await doc_ref.delete()

    async def _coll(self, name: str):
        fs = await self._firestore()
        return fs.collection(name)

    async def _firestore(self):
        if self._fs is not None:
            return self._fs
        # Lazy import so module import doesn't pull in google-cloud-firestore
        # for users running stdio-mode locally.
        from google.cloud.firestore_v1 import AsyncClient

        kwargs: dict[str, Any] = {"project": self._project_id}
        if self._database_id and self._database_id != "(default)":
            kwargs["database"] = self._database_id
        self._fs = AsyncClient(**kwargs)
        return self._fs

    async def _kms_client(self):
        if self._kms is not None:
            return self._kms
        from google.cloud.kms_v1 import KeyManagementServiceAsyncClient

        self._kms = KeyManagementServiceAsyncClient()
        return self._kms

    async def _encrypt(self, plaintext: str) -> bytes:
        kms = await self._kms_client()
        resp = await kms.encrypt(
            request={"name": self._kms_key_name, "plaintext": plaintext.encode("utf-8")}
        )
        return resp.ciphertext

    async def _decrypt(self, ciphertext: bytes) -> str:
        kms = await self._kms_client()
        resp = await kms.decrypt(
            request={"name": self._kms_key_name, "ciphertext": ciphertext}
        )
        return resp.plaintext.decode("utf-8")
