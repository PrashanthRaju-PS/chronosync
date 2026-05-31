"""One ticker failing mid-batch must not abort the others (FR-3.2) and the
terminal error must land in sync_state.last_error (§8 #6).

Mocks the yfinance provider so one ticker raises PermanentProviderError while
the rest return valid bars.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from chronosync.db import repositories as repos
from chronosync.exceptions import PermanentProviderError
from chronosync.providers import yfinance_feed
from chronosync.sync import planner, worker


@pytest.mark.functional
@pytest.mark.requires_docker
@pytest.mark.asyncio
async def test_one_failure_does_not_block_others(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    # Seed five test instruments.
    tickers = ("PF_OK1", "PF_OK2", "PF_BOOM", "PF_OK3", "PF_OK4")
    await repos.upsert_instruments(
        db_session,
        [
            {
                "ticker": t,
                "exchange": "NSE",
                "asset_class": "EQUITY",
                "country_code": "IN",
                "currency": "INR",
                "isin": None,
                "is_active": True,
            }
            for t in tickers
        ],
    )
    await db_session.commit()

    # Capture IDs as plain UUIDs before running the worker so we can re-query
    # safely without dragging expired ORM objects across session boundaries.
    inst_ids: dict[str, object] = {}
    for t in tickers:
        inst = await repos.get_instrument(db_session, t)
        assert inst is not None
        inst_ids[t] = inst.id

    good_df = pd.DataFrame(
        [{"Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5, "Adj Close": 100.5, "Volume": 1000}],
        index=pd.to_datetime(["2024-01-02"]),
    )

    def fake_download(symbol: str, *_a, **_k):  # noqa: ANN001, ANN202
        if symbol.startswith("PF_BOOM"):
            raise PermanentProviderError(f"bad symbol: {symbol}")
        return good_df

    monkeypatch.setattr(yfinance_feed, "_yf_download_sync", fake_download)

    feed = yfinance_feed.YFinanceFeed(concurrency=2, retry_max_attempts=1)
    tasks = [
        planner.FetchTask(
            instrument_id=iid,  # type: ignore[arg-type]
            ticker=t,
            exchange="NSE",
            feed_name="yfinance",
            from_date=date(2024, 1, 2),
            to_date=date(2024, 1, 2),
        )
        for t, iid in inst_ids.items()
    ]

    summary = await worker.run(tasks, feed=feed, concurrency=2)

    # 5 tasks: 4 succeed (1 row each), 1 errors.
    assert summary.scanned == 5
    assert summary.upserted == 4
    assert summary.errored == 1

    # Re-query bars by UUID (no ORM-object reuse across sessions).
    for t, iid in inst_ids.items():
        bars = await repos.daily_bars_range(
            db_session, instrument_id=iid, frm=date(2024, 1, 1), to=date(2024, 1, 31)  # type: ignore[arg-type]
        )
        if t == "PF_BOOM":
            assert bars == []
        else:
            assert len(bars) == 1
            assert bars[0].close == Decimal("100.500000")

    # Terminal error recorded for the failed ticker only.
    boom_status = await repos.sync_status_for(
        db_session, instrument_id=inst_ids["PF_BOOM"], feed_name="yfinance"  # type: ignore[arg-type]
    )
    assert len(boom_status) == 1
    assert boom_status[0].last_error is not None
    assert "permanent" in boom_status[0].last_error.lower() or "bad symbol" in boom_status[0].last_error.lower()
    assert boom_status[0].last_synced_ts is None

    # Survivors have clean state.
    ok1_status = await repos.sync_status_for(
        db_session, instrument_id=inst_ids["PF_OK1"], feed_name="yfinance"  # type: ignore[arg-type]
    )
    assert len(ok1_status) == 1
    assert ok1_status[0].last_error is None
    assert ok1_status[0].last_synced_ts == date(2024, 1, 2)
