"""initial schema: instruments, daily_bars, intraday_bars, sync_state, users/accounts/credentials

Revision ID: 0001
Revises:
Create Date: 2026-05-31
"""
from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "timescaledb"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    op.execute(
        "CREATE TYPE asset_class AS ENUM "
        "('EQUITY','FUTURE','OPTION','BOND','FX','CRYPTO')"
    )

    op.execute(
        """
        CREATE TABLE instruments (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            ticker           TEXT NOT NULL,
            exchange         TEXT NOT NULL,
            asset_class      asset_class NOT NULL DEFAULT 'EQUITY',
            country_code     CHAR(2) NOT NULL,
            currency         CHAR(3) NOT NULL,
            isin             TEXT,
            market_cap       NUMERIC(20, 2),
            market_cap_as_of DATE,
            is_active        BOOLEAN NOT NULL DEFAULT TRUE,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (ticker, exchange)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_instruments_active ON instruments (is_active) WHERE is_active"
    )
    op.execute(
        "CREATE INDEX idx_instruments_exchange_active ON instruments (exchange, is_active)"
    )
    op.execute(
        "CREATE INDEX idx_instruments_isin ON instruments (isin) WHERE isin IS NOT NULL"
    )

    op.execute(
        """
        CREATE TABLE daily_bars (
            instrument_id  UUID NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
            ts             DATE NOT NULL,
            open           NUMERIC(20, 6) NOT NULL,
            high           NUMERIC(20, 6) NOT NULL,
            low            NUMERIC(20, 6) NOT NULL,
            close          NUMERIC(20, 6) NOT NULL,
            volume         BIGINT NOT NULL,
            vwap           NUMERIC(20, 6),
            adj_factor     NUMERIC(20, 10) NOT NULL DEFAULT 1.0,
            open_interest  BIGINT,
            feed_name      TEXT NOT NULL,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (instrument_id, ts)
        )
        """
    )
    op.execute(
        "SELECT create_hypertable('daily_bars', 'ts', chunk_time_interval => INTERVAL '1 year')"
    )
    op.execute(
        "ALTER TABLE daily_bars SET ("
        "timescaledb.compress, timescaledb.compress_segmentby = 'instrument_id')"
    )
    op.execute("SELECT add_compression_policy('daily_bars', INTERVAL '90 days')")

    op.execute(
        """
        CREATE TABLE intraday_bars (
            instrument_id  UUID NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
            ts             TIMESTAMPTZ NOT NULL,
            interval       TEXT NOT NULL,
            open           NUMERIC(20, 6) NOT NULL,
            high           NUMERIC(20, 6) NOT NULL,
            low            NUMERIC(20, 6) NOT NULL,
            close          NUMERIC(20, 6) NOT NULL,
            volume         BIGINT NOT NULL,
            vwap           NUMERIC(20, 6),
            feed_name      TEXT NOT NULL,
            created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (instrument_id, ts, interval)
        )
        """
    )
    op.execute(
        "SELECT create_hypertable('intraday_bars', 'ts', chunk_time_interval => INTERVAL '7 days')"
    )
    op.execute(
        "ALTER TABLE intraday_bars SET ("
        "timescaledb.compress, timescaledb.compress_segmentby = 'instrument_id,interval')"
    )
    op.execute("SELECT add_compression_policy('intraday_bars', INTERVAL '7 days')")
    op.execute("SELECT add_retention_policy('intraday_bars', INTERVAL '730 days')")

    op.execute(
        """
        CREATE TABLE sync_state (
            instrument_id        UUID NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
            feed_name            TEXT NOT NULL,
            last_synced_ts       DATE,
            last_run_started_at  TIMESTAMPTZ,
            last_run_finished_at TIMESTAMPTZ,
            last_error           TEXT,
            attempt_count        INT NOT NULL DEFAULT 0,
            PRIMARY KEY (instrument_id, feed_name)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE users (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email        TEXT NOT NULL UNIQUE,
            display_name TEXT,
            is_active    BOOLEAN NOT NULL DEFAULT TRUE,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE accounts (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            vendor        TEXT NOT NULL,
            account_label TEXT NOT NULL,
            is_active     BOOLEAN NOT NULL DEFAULT TRUE,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (user_id, vendor, account_label)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE vendor_credentials (
            account_id  UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
            key_name    TEXT NOT NULL,
            secret_ref  TEXT NOT NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            rotated_at  TIMESTAMPTZ,
            PRIMARY KEY (account_id, key_name)
        )
        """
    )


def downgrade() -> None:
    for table in [
        "vendor_credentials",
        "accounts",
        "users",
        "sync_state",
        "intraday_bars",
        "daily_bars",
        "instruments",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("DROP TYPE IF EXISTS asset_class")
