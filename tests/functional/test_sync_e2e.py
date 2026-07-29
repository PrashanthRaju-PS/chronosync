"""End-to-end: seed-style instrument insert → worker → bars persisted → re-run is idempotent."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from chronosync.db import repositories as repos
from chronosync.providers import yfinance_feed
from chronosync.sync import planner, worker


@pytest.mark.functional
@pytest.mark.requires_docker
@pytest.mark.asyncio
async def test_full_iteration_and_idempotency(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    # Seed one instrument directly (simpler than running Bhavcopy in test).
    await repos.upsert_instruments(
        db_session,
        [
            {
                "ticker": "TESTCO",
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

    inst = await repos.get_instrument(db_session, "TESTCO")
    assert inst is not None

    # Stub yfinance to return three deterministic rows.
    df = pd.DataFrame(
        [
            {
                "Open": 100.0,
                "High": 105.0,
                "Low": 99.0,
                "Close": 104.0,
                "Adj Close": 104.0,
                "Volume": 1000,
            },
            {
                "Open": 104.0,
                "High": 110.0,
                "Low": 103.0,
                "Close": 108.0,
                "Adj Close": 108.0,
                "Volume": 2000,
            },
            {
                "Open": 108.0,
                "High": 112.0,
                "Low": 107.0,
                "Close": 111.0,
                "Adj Close": 111.0,
                "Volume": 1500,
            },
        ],
        index=pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
    )
    monkeypatch.setattr(yfinance_feed, "_yf_download_sync", lambda *a, **k: df)

    feed = yfinance_feed.YFinanceFeed(concurrency=2, retry_max_attempts=1)

    tasks = planner.split_backfill(
        inst.id,
        inst.ticker,
        inst.exchange,
        "yfinance",
        date(2024, 1, 2),
        date(2024, 1, 4),
        batch_days=10,
    )

    summary1 = await worker.run(tasks, feed=feed, concurrency=2)
    assert summary1.upserted == 3
    assert summary1.errored == 0

    bars = await repos.daily_bars_range(
        db_session, instrument_id=inst.id, frm=date(2024, 1, 1), to=date(2024, 1, 10)
    )
    assert len(bars) == 3
    assert bars[-1].close == Decimal("111.000000")

    # Second run — same window, same rows. Should be idempotent.
    summary2 = await worker.run(tasks, feed=feed, concurrency=2)
    assert summary2.errored == 0
    bars2 = await repos.daily_bars_range(
        db_session, instrument_id=inst.id, frm=date(2024, 1, 1), to=date(2024, 1, 10)
    )
    assert len(bars2) == 3  # no duplicates
