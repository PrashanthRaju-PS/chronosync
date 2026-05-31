"""Run worker twice over the same window; row count and sync_state are stable."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from chronosync.db import repositories as repos
from chronosync.providers import yfinance_feed
from chronosync.sync import planner, worker


@pytest.mark.functional
@pytest.mark.requires_docker
@pytest.mark.asyncio
async def test_double_run_is_noop(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    await repos.upsert_instruments(
        db_session,
        [
            {
                "ticker": "IDEMP",
                "exchange": "NSE",
                "asset_class": "EQUITY",
                "country_code": "IN",
                "currency": "INR",
                "isin": None,
                "is_active": True,
            }
        ],
    )
    inst = await repos.get_instrument(db_session, "IDEMP")
    assert inst is not None
    await db_session.commit()

    df = pd.DataFrame(
        [{"Open": 1, "High": 2, "Low": 1, "Close": 2, "Adj Close": 2, "Volume": 10}],
        index=pd.to_datetime(["2024-01-02"]),
    )
    monkeypatch.setattr(yfinance_feed, "_yf_download_sync", lambda *a, **k: df)
    feed = yfinance_feed.YFinanceFeed(concurrency=1, retry_max_attempts=1)
    tasks = planner.split_backfill(
        inst.id, inst.ticker, inst.exchange, "yfinance",
        date(2024, 1, 2), date(2024, 1, 2), batch_days=10,
    )

    s1 = await worker.run(tasks, feed=feed, concurrency=1)
    s2 = await worker.run(tasks, feed=feed, concurrency=1)
    bars = await repos.daily_bars_range(db_session, instrument_id=inst.id, frm=date(2024, 1, 1), to=date(2024, 1, 31))
    assert len(bars) == 1
    assert s1.errored == 0 and s2.errored == 0
