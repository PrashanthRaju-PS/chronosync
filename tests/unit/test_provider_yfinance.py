"""yfinance adapter: DataFrame→BarRow mapping, adjusted/raw math, error mapping.

We monkeypatch the underlying yfinance.download to avoid network/vcr complexity.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from chronosync.exceptions import PermanentProviderError, RetriableProviderError
from chronosync.providers import yfinance_feed


def _df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df["date"])
    df = df.drop(columns=["date"])
    return df


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_daily_bars_maps_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    df = _df(
        [
            {"date": "2024-01-02", "Open": 100, "High": 105, "Low": 99, "Close": 104, "Adj Close": 104, "Volume": 1_000},
            {"date": "2024-01-03", "Open": 104, "High": 110, "Low": 103, "Close": 108, "Adj Close": 54, "Volume": 2_000},
        ]
    )
    monkeypatch.setattr(yfinance_feed, "_yf_download_sync", lambda *a, **k: df)

    feed = yfinance_feed.YFinanceFeed(concurrency=1)
    bars = [b async for b in feed.fetch_daily_bars("FOO", "NSE", date(2024, 1, 2), date(2024, 1, 3))]
    assert len(bars) == 2
    assert bars[0].close == Decimal("104")
    assert bars[0].adj_factor == Decimal("1.0000000000")
    # 54 / 108 = 0.5 — represents a 2:1 split
    assert bars[1].adj_factor == Decimal("0.5000000000")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_df_yields_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(yfinance_feed, "_yf_download_sync", lambda *a, **k: pd.DataFrame())
    feed = yfinance_feed.YFinanceFeed(concurrency=1)
    bars = [b async for b in feed.fetch_daily_bars("FOO", "NSE", date(2024, 1, 2), date(2024, 1, 3))]
    assert bars == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limit_raises_retriable(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a, **_k):  # noqa: ANN001, ANN201
        raise RuntimeError("HTTP 429 too many requests")

    # Patch the underlying yf.download so _yf_download_sync's exception-mapping
    # logic runs and converts the RuntimeError into RetriableProviderError.
    monkeypatch.setattr(yfinance_feed.yf, "download", boom)
    feed = yfinance_feed.YFinanceFeed(concurrency=1, retry_max_attempts=2, retry_base_delay_s=0.01)
    with pytest.raises(RetriableProviderError):
        _ = [b async for b in feed.fetch_daily_bars("FOO", "NSE", date(2024, 1, 2), date(2024, 1, 3))]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_exchange_permanent() -> None:
    feed = yfinance_feed.YFinanceFeed(concurrency=1)
    with pytest.raises(PermanentProviderError):
        _ = [b async for b in feed.fetch_daily_bars("FOO", "MARS", date(2024, 1, 2), date(2024, 1, 3))]
