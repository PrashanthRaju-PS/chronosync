"""Shared fixtures. See docs/LLD.md §12.3."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio

os.environ.setdefault("CHRONOSYNC_DB__DSN", "postgresql+asyncpg://chrono:chrono@localhost:5432/chronosync_test")
os.environ.setdefault("CHRONOSYNC_VAULT_KEY", "test-vault-key-not-for-prod")


@pytest.fixture(scope="session")
def event_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def pg_container() -> Iterator[str]:
    """TimescaleDB testcontainer. Yields the asyncpg DSN. Skipped if Docker absent."""
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:  # pragma: no cover
        pytest.skip("testcontainers not installed")

    image = "timescale/timescaledb:latest-pg15"
    try:
        container = PostgresContainer(image=image, dbname="chronosync_test", username="chrono", password="chrono")
        container.start()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"docker unavailable: {e}")
    try:
        dsn = container.get_connection_url().replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        os.environ["CHRONOSYNC_DB__DSN"] = dsn

        # Run migrations via the active venv's Python (alembic isn't on PATH outside it).
        env = os.environ.copy()
        env["CHRONOSYNC_DB__DSN"] = dsn
        subprocess.check_call(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=Path(__file__).resolve().parents[1],
            env=env,
        )
        yield dsn
    finally:
        container.stop()


@pytest_asyncio.fixture
async def db_session(pg_container: str) -> AsyncIterator:
    from chronosync.config import DatabaseSettings
    from chronosync.db import engine as db_engine

    db_engine.init_engine(DatabaseSettings(dsn=pg_container))
    try:
        async with db_engine.session_scope() as s:
            yield s
    finally:
        await db_engine.dispose()


@pytest.fixture
def tmp_vault(tmp_path: Path):  # type: ignore[no-untyped-def]
    from chronosync.secrets.local import LocalEncryptedBackend

    os.environ["CHRONOSYNC_VAULT_KEY"] = "test-vault-key-not-for-prod"
    return LocalEncryptedBackend(path=tmp_path / "vault.enc", master_key_env="CHRONOSYNC_VAULT_KEY")
