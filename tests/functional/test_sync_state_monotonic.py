"""mark_sync_success must never regress last_synced_ts — backfill chunks land out-of-order."""

from __future__ import annotations

from datetime import date

import pytest

from chronosync.db import repositories as repos


@pytest.mark.functional
@pytest.mark.requires_docker
@pytest.mark.asyncio
async def test_last_synced_ts_is_monotonic(db_session) -> None:
    await repos.upsert_instruments(
        db_session,
        [
            {
                "ticker": "MONOTONIC",
                "exchange": "NSE",
                "asset_class": "EQUITY",
                "country_code": "IN",
                "currency": "INR",
                "isin": None,
                "is_active": True,
            }
        ],
    )
    inst = await repos.get_instrument(db_session, "MONOTONIC")
    assert inst is not None
    await db_session.commit()

    # Simulate batches landing out of order: newest first, then an older one.
    await repos.mark_sync_success(db_session, instrument_id=inst.id, feed_name="yfinance", last_ts=date(2026, 5, 29))
    await db_session.commit()
    await repos.mark_sync_success(db_session, instrument_id=inst.id, feed_name="yfinance", last_ts=date(2026, 3, 25))
    await db_session.commit()

    status = await repos.sync_status_for(db_session, instrument_id=inst.id, feed_name="yfinance")
    assert len(status) == 1
    assert status[0].last_synced_ts == date(2026, 5, 29)  # newer value preserved
