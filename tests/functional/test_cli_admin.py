"""CLI admin flow: user → account → cred set. Verifies vault stores value; DB stores only ref."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from chronosync.config import VaultSettings
from chronosync.db import repositories as repos
from chronosync.secrets import build_backend


@pytest.mark.functional
@pytest.mark.requires_docker
@pytest.mark.asyncio
async def test_admin_cred_flow_stores_ref_only(db_session, tmp_path: Path) -> None:
    # Seed user + account directly (CLI exercises typer; here we test the data path).
    user = await repos.add_user(db_session, email="t@example.com", display_name="T")
    acct = await repos.add_account(db_session, user_id=user.id, vendor="kite", label="main")
    await db_session.commit()

    os.environ["CHRONOSYNC_VAULT_KEY"] = "functional-test-key"
    settings = VaultSettings(
        backend="local", path=tmp_path / "vault.enc", master_key_env="CHRONOSYNC_VAULT_KEY"
    )
    backend = build_backend(settings)

    ref = f"kite/{acct.id}/api_key"
    await backend.put(ref, "super-secret-key")
    await repos.upsert_credential(
        db_session, account_id=acct.id, key_name="api_key", secret_ref=ref
    )
    await db_session.commit()

    cred = await repos.get_credential(db_session, account_id=acct.id, key_name="api_key")
    assert cred is not None
    assert cred.secret_ref == ref
    # DB must not contain the plaintext.
    file_bytes = (tmp_path / "vault.enc").read_bytes()
    assert b"super-secret-key" not in file_bytes  # encrypted at rest
    # Vault still returns the value.
    assert await backend.get(ref) == "super-secret-key"
