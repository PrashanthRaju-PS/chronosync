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


def _existing_dsn_reachable(dsn: str) -> bool:
    """Best-effort sync ping. Returns True if we can open a TCP connection
    AND select 1. Used by CI where a service container is already running."""
    import asyncio

    try:
        import asyncpg
    except ImportError:  # pragma: no cover
        return False

    async def _ping() -> bool:
        try:
            # asyncpg wants postgresql:// not postgresql+asyncpg://
            url = dsn.replace("postgresql+asyncpg://", "postgresql://")
            conn = await asyncio.wait_for(asyncpg.connect(url), timeout=3.0)
            try:
                await conn.fetchval("SELECT 1")
            finally:
                await conn.close()
            return True
        except Exception:  # noqa: BLE001
            return False

    return asyncio.run(_ping())


def _apply_migrations(dsn: str) -> None:
    env = os.environ.copy()
    env["CHRONOSYNC_DB__DSN"] = dsn
    subprocess.check_call(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
    )


@pytest.fixture(scope="session")
def pg_container() -> Iterator[str]:
    """Yield an asyncpg DSN pointed at a running TimescaleDB.

    Strategy:
    1. If CHRONOSYNC_DB__DSN is already reachable (CI service container,
       local docker-compose), use it as-is and apply migrations idempotently.
    2. Otherwise spin up testcontainers/timescaledb.
    3. Otherwise skip — functional tests need a real DB.
    """
    configured = os.environ.get("CHRONOSYNC_DB__DSN", "")
    if configured and _existing_dsn_reachable(configured):
        _apply_migrations(configured)
        yield configured
        return

    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:  # pragma: no cover
        pytest.skip("testcontainers not installed and no reachable DB")

    image = "timescale/timescaledb:latest-pg15"
    try:
        container = PostgresContainer(image=image, dbname="chronosync_test", username="chrono", password="chrono")
        container.start()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"docker unavailable and no reachable DB: {e}")
    try:
        dsn = container.get_connection_url().replace("postgresql+psycopg2://", "postgresql+asyncpg://")
        os.environ["CHRONOSYNC_DB__DSN"] = dsn
        _apply_migrations(dsn)
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
