"""OS keyring backend (Windows Credential Manager / macOS Keychain / Linux Secret Service)."""

from __future__ import annotations

import asyncio
from typing import ClassVar

import keyring

from chronosync.exceptions import SecretNotFound

_SERVICE = "chronosync"


class KeyringBackend:
    backend_name: ClassVar[str] = "keyring"

    async def get(self, ref: str) -> str:
        val = await asyncio.to_thread(keyring.get_password, _SERVICE, ref)
        if val is None:
            raise SecretNotFound(ref)
        return val

    async def put(self, ref: str, value: str) -> None:
        await asyncio.to_thread(keyring.set_password, _SERVICE, ref, value)

    async def rotate(self, ref: str, new_value: str) -> None:
        await self.put(ref, new_value)

    async def delete(self, ref: str) -> None:
        try:
            await asyncio.to_thread(keyring.delete_password, _SERVICE, ref)
        except keyring.errors.PasswordDeleteError as e:
            raise SecretNotFound(ref) from e
