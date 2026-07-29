"""Local encrypted JSON vault using Fernet over PBKDF2-derived key.

File layout (decrypted JSON):
{
    "secrets": {
        "<ref>": {"value": "<plain>", "created_at": "...", "rotated_at": null}
    }
}

Encrypted file is a single Fernet token plus a fixed-length salt prefix.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets as pysecrets
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from chronosync.exceptions import SecretNotFoundError, VaultUnlockedError

_SALT_LEN = 16
_PBKDF_ITERS = 200_000


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=_PBKDF_ITERS,
    )
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


class LocalEncryptedBackend:
    backend_name: ClassVar[str] = "local"

    def __init__(self, path: Path, master_key_env: str) -> None:
        self._path = path
        self._key_env = master_key_env
        self._lock = asyncio.Lock()

    # ---- helpers ----

    def _passphrase(self) -> str:
        val = os.environ.get(self._key_env)
        if not val:
            raise VaultUnlockedError(f"missing master key env var: {self._key_env}")
        return val

    def _read_blob(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"secrets": {}}
        raw = self._path.read_bytes()
        salt, token = raw[:_SALT_LEN], raw[_SALT_LEN:]
        f = Fernet(_derive_key(self._passphrase(), salt))
        try:
            plain = f.decrypt(token)
        except InvalidToken as e:
            raise VaultUnlockedError("invalid master key for existing vault") from e
        return json.loads(plain)

    def _write_blob(self, blob: dict[str, Any]) -> None:
        salt = pysecrets.token_bytes(_SALT_LEN)
        f = Fernet(_derive_key(self._passphrase(), salt))
        token = f.encrypt(json.dumps(blob).encode("utf-8"))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_bytes(salt + token)
        try:
            os.chmod(tmp, 0o600)
        except (OSError, NotImplementedError):
            pass
        os.replace(tmp, self._path)

    # ---- API ----

    async def get(self, ref: str) -> str:
        async with self._lock:
            blob = await asyncio.to_thread(self._read_blob)
        entry = blob.get("secrets", {}).get(ref)
        if entry is None:
            raise SecretNotFoundError(ref)
        return str(entry["value"])

    async def put(self, ref: str, value: str) -> None:
        async with self._lock:
            blob = await asyncio.to_thread(self._read_blob)
            blob.setdefault("secrets", {})[ref] = {
                "value": value,
                "created_at": datetime.now(UTC).isoformat(),
                "rotated_at": None,
            }
            await asyncio.to_thread(self._write_blob, blob)

    async def rotate(self, ref: str, new_value: str) -> None:
        async with self._lock:
            blob = await asyncio.to_thread(self._read_blob)
            entry = blob.get("secrets", {}).get(ref)
            now_iso = datetime.now(UTC).isoformat()
            if entry is None:
                blob.setdefault("secrets", {})[ref] = {
                    "value": new_value,
                    "created_at": now_iso,
                    "rotated_at": now_iso,
                }
            else:
                entry["value"] = new_value
                entry["rotated_at"] = now_iso
            await asyncio.to_thread(self._write_blob, blob)

    async def delete(self, ref: str) -> None:
        async with self._lock:
            blob = await asyncio.to_thread(self._read_blob)
            if ref in blob.get("secrets", {}):
                del blob["secrets"][ref]
                await asyncio.to_thread(self._write_blob, blob)
