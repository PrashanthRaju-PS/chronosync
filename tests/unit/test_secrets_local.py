"""Local encrypted vault round-trip + key + delete behaviours."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from chronosync.exceptions import SecretNotFound, VaultUnlocked
from chronosync.secrets.local import LocalEncryptedBackend


@pytest.mark.unit
@pytest.mark.asyncio
async def test_put_get_roundtrip(tmp_path: Path) -> None:
    os.environ["TEST_VAULT_KEY"] = "correct horse battery staple"
    v = LocalEncryptedBackend(path=tmp_path / "v.enc", master_key_env="TEST_VAULT_KEY")
    await v.put("kite/acct/api_key", "abc123")
    assert await v.get("kite/acct/api_key") == "abc123"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_wrong_key_rejected(tmp_path: Path) -> None:
    os.environ["TEST_VAULT_KEY"] = "right-key"
    v = LocalEncryptedBackend(path=tmp_path / "v.enc", master_key_env="TEST_VAULT_KEY")
    await v.put("x", "y")
    os.environ["TEST_VAULT_KEY"] = "wrong-key"
    with pytest.raises(VaultUnlocked):
        await v.get("x")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_key_env(tmp_path: Path) -> None:
    os.environ.pop("MISSING_KEY", None)
    v = LocalEncryptedBackend(path=tmp_path / "v.enc", master_key_env="MISSING_KEY")
    with pytest.raises(VaultUnlocked):
        await v.put("k", "v")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_and_not_found(tmp_path: Path) -> None:
    os.environ["TEST_VAULT_KEY"] = "k"
    v = LocalEncryptedBackend(path=tmp_path / "v.enc", master_key_env="TEST_VAULT_KEY")
    await v.put("a", "1")
    await v.delete("a")
    with pytest.raises(SecretNotFound):
        await v.get("a")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rotate_updates_value(tmp_path: Path) -> None:
    os.environ["TEST_VAULT_KEY"] = "k"
    v = LocalEncryptedBackend(path=tmp_path / "v.enc", master_key_env="TEST_VAULT_KEY")
    await v.put("a", "old")
    await v.rotate("a", "new")
    assert await v.get("a") == "new"
