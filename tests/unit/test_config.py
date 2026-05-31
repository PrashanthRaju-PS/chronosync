"""Settings parse env + .env, with sensible defaults."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from chronosync.config import Settings, get_settings


@pytest.mark.unit
def test_defaults_with_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    # _env_file=None bypasses any local .env so we assert on pure code defaults.
    monkeypatch.setenv("CHRONOSYNC_DB__DSN", "postgresql+asyncpg://u:p@h:5432/db")
    monkeypatch.delenv("CHRONOSYNC_VAULT__BACKEND", raising=False)
    monkeypatch.delenv("CHRONOSYNC_SYNC__EXCHANGES", raising=False)
    get_settings.cache_clear()
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.db.pool_max_size == 20
    assert s.providers.default_feed == "yfinance"
    assert s.vault.backend == "local"
    assert s.sync.exchanges == ["NSE", "BSE"]


@pytest.mark.unit
def test_nested_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CHRONOSYNC_DB__DSN", "postgresql+asyncpg://u:p@h:5432/db")
    monkeypatch.setenv("CHRONOSYNC_DB__POOL_MAX_SIZE", "50")
    monkeypatch.setenv("CHRONOSYNC_PROVIDERS__YFINANCE_CONCURRENCY", "16")
    monkeypatch.setenv("CHRONOSYNC_VAULT__PATH", str(tmp_path / "v.enc"))
    get_settings.cache_clear()
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.db.pool_max_size == 50
    assert s.providers.yfinance_concurrency == 16
    assert s.vault.path == tmp_path / "v.enc"


@pytest.mark.unit
def test_missing_dsn_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in list(os.environ):
        if k.startswith("CHRONOSYNC_DB__"):
            monkeypatch.delenv(k, raising=False)
    get_settings.cache_clear()
    with pytest.raises(Exception):
        Settings(_env_file=None)  # type: ignore[call-arg]
