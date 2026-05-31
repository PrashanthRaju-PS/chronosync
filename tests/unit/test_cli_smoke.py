"""Typer CLI runner smoke tests — FR-2.6 + FR-5.1..5.7.

Exercises every command's argument parsing + --help output via
typer.testing.CliRunner. DB/provider boundaries are mocked so these stay
unit-test fast (no Docker).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from typer.testing import CliRunner

from chronosync.cli import app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def _isolate_cli(monkeypatch: pytest.MonkeyPatch, tmp_path):  # type: ignore[no-untyped-def]
    """Stub everything that would touch real DBs / providers / vaults so the
    CLI runner can execute each command path without external side effects."""
    # _bootstrap() calls configure_logging + init_engine. Stub both.
    monkeypatch.setattr("chronosync.cli.configure_logging", lambda *_a, **_k: None)
    monkeypatch.setattr("chronosync.cli.db_engine.init_engine", lambda *_a, **_k: MagicMock())

    # All async DB calls become AsyncMocks; session_scope returns a context manager.
    @asynccontextmanager
    async def fake_scope():
        yield MagicMock()

    monkeypatch.setattr("chronosync.cli.db_engine.session_scope", fake_scope)
    monkeypatch.setattr("chronosync.cli.db_engine.ping", AsyncMock(return_value=None))
    monkeypatch.setattr("chronosync.cli.db_engine.dispose", AsyncMock(return_value=None))

    # Vault backend
    fake_backend = MagicMock()
    fake_backend.put = AsyncMock(return_value=None)
    fake_backend.rotate = AsyncMock(return_value=None)
    fake_backend.delete = AsyncMock(return_value=None)
    monkeypatch.setattr("chronosync.cli.build_backend", lambda *_a, **_k: fake_backend)

    # Settings — force a known path so .env doesn't leak in.
    from chronosync.config import (
        ApiSettings,
        DatabaseSettings,
        LoggingSettings,
        ProviderSettings,
        Settings,
        SyncSettings,
        VaultSettings,
    )

    fake_settings = Settings(
        db=DatabaseSettings(dsn="postgresql+asyncpg://u:p@h:5432/d"),
        vault=VaultSettings(path=tmp_path / "v.enc"),
        providers=ProviderSettings(),
        sync=SyncSettings(),
        api=ApiSettings(),
        logging=LoggingSettings(),
        _env_file=None,  # type: ignore[call-arg]
    )
    monkeypatch.setattr("chronosync.cli.get_settings", lambda: fake_settings)


# ---------- top-level --help ----------


@pytest.mark.unit
def test_root_help(runner: CliRunner) -> None:
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    for cmd in ("run", "seed", "backfill", "status", "admin", "refresh-meta"):
        assert cmd in res.stdout


# ---------- per-command --help ----------


@pytest.mark.unit
@pytest.mark.parametrize(
    "args",
    [
        ["run", "--help"],
        ["seed", "--help"],
        ["backfill", "--help"],
        ["status", "--help"],
        ["refresh-meta", "--help"],
        ["admin", "--help"],
        ["admin", "user", "--help"],
        ["admin", "user", "add", "--help"],
        ["admin", "user", "list", "--help"],
        ["admin", "user", "disable", "--help"],
        ["admin", "account", "--help"],
        ["admin", "account", "add", "--help"],
        ["admin", "account", "list", "--help"],
        ["admin", "account", "remove", "--help"],
        ["admin", "cred", "--help"],
        ["admin", "cred", "set", "--help"],
        ["admin", "cred", "rotate", "--help"],
        ["admin", "cred", "delete", "--help"],
    ],
)
def test_subcommand_help(runner: CliRunner, args: list[str]) -> None:
    res = runner.invoke(app, args)
    assert res.exit_code == 0, f"--help failed for {args}: {res.output}"


# ---------- argument validation ----------


@pytest.mark.unit
def test_backfill_rejects_inverted_date_range(runner: CliRunner) -> None:
    res = runner.invoke(
        app,
        ["backfill", "--from", "2026-05-01", "--to", "2026-01-01", "--exchange", "NSE"],
    )
    assert res.exit_code == 2
    assert "--from must be on or before --to" in res.stdout


@pytest.mark.unit
def test_backfill_invalid_date_raises(runner: CliRunner) -> None:
    res = runner.invoke(
        app,
        ["backfill", "--from", "not-a-date", "--to", "2026-01-01"],
    )
    assert res.exit_code != 0


# ---------- admin user add wires through ----------


@pytest.mark.unit
def test_admin_user_add_invokes_repo(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    fake_user = MagicMock()
    fake_user.id = uuid4()
    fake_user.email = "t@example.com"

    async def fake_add(_s, *, email, display_name):  # type: ignore[no-untyped-def]
        captured["email"] = email
        captured["display_name"] = display_name
        return fake_user

    monkeypatch.setattr("chronosync.cli.repos.add_user", fake_add)

    res = runner.invoke(app, ["admin", "user", "add", "t@example.com", "--display-name", "T"])
    assert res.exit_code == 0
    assert captured == {"email": "t@example.com", "display_name": "T"}
    assert "user created" in res.stdout


# ---------- admin cred set reads stdin via "-" ----------


@pytest.mark.unit
def test_admin_cred_set_value_from_stdin(runner: CliRunner, monkeypatch: pytest.MonkeyPatch) -> None:
    aid = uuid4()
    upsert_seen: dict = {}

    fake_account = MagicMock()
    fake_account.id = aid
    fake_account.vendor = "kite"
    fake_account.account_label = "main"

    async def fake_list_accounts(_s, *, user_id=None):  # type: ignore[no-untyped-def]
        return [fake_account]

    async def fake_upsert(_s, *, account_id, key_name, secret_ref, rotated=False):  # type: ignore[no-untyped-def]
        upsert_seen.update(
            {"account_id": account_id, "key_name": key_name, "secret_ref": secret_ref}
        )

    monkeypatch.setattr("chronosync.cli.repos.list_accounts", fake_list_accounts)
    monkeypatch.setattr("chronosync.cli.repos.upsert_credential", fake_upsert)

    res = runner.invoke(
        app,
        ["admin", "cred", "set", "--account-id", str(aid), "--key", "api_key", "--value", "-"],
        input="from-stdin-secret\n",
    )
    assert res.exit_code == 0, res.output
    assert upsert_seen["key_name"] == "api_key"
    assert upsert_seen["secret_ref"] == f"kite/{aid}/api_key"
    # The plaintext value must NOT appear in stdout (CLI prints "value redacted").
    assert "from-stdin-secret" not in res.stdout
