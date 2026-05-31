"""(ticker, exchange) uniqueness — FR-1.4.

Raw INSERT (not the repo upsert) must violate the unique constraint when the
pair already exists. Validates the schema, not the upsert path.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from chronosync.db import repositories as repos


@pytest.mark.functional
@pytest.mark.requires_docker
@pytest.mark.asyncio
async def test_duplicate_ticker_exchange_pair_rejected(db_session) -> None:
    await repos.upsert_instruments(
        db_session,
        [
            {
                "ticker": "UNIQ_X",
                "exchange": "NSE",
                "asset_class": "EQUITY",
                "country_code": "IN",
                "currency": "INR",
                "isin": None,
                "is_active": True,
            }
        ],
    )
    await db_session.commit()

    # Same (ticker, exchange) pair via raw INSERT bypasses the upsert
    # and must hit the unique constraint.
    with pytest.raises(IntegrityError):
        await db_session.execute(
            text(
                "INSERT INTO instruments (ticker, exchange, asset_class, country_code, currency, is_active) "
                "VALUES (:t, :e, 'EQUITY', 'IN', 'INR', true)"
            ),
            {"t": "UNIQ_X", "e": "NSE"},
        )
        await db_session.flush()


@pytest.mark.functional
@pytest.mark.requires_docker
@pytest.mark.asyncio
async def test_same_ticker_different_exchange_allowed(db_session) -> None:
    # Same ticker is fine when exchange differs.
    await repos.upsert_instruments(
        db_session,
        [
            {
                "ticker": "MULTIEX",
                "exchange": "NSE",
                "asset_class": "EQUITY",
                "country_code": "IN",
                "currency": "INR",
                "isin": None,
                "is_active": True,
            },
            {
                "ticker": "MULTIEX",
                "exchange": "BSE",
                "asset_class": "EQUITY",
                "country_code": "IN",
                "currency": "INR",
                "isin": None,
                "is_active": True,
            },
        ],
    )
    await db_session.commit()

    res = await db_session.execute(
        text("SELECT exchange FROM instruments WHERE ticker = :t ORDER BY exchange"),
        {"t": "MULTIEX"},
    )
    exchanges = [r[0] for r in res]
    assert exchanges == ["BSE", "NSE"]
