"""upsert_daily_bars must chunk to stay under asyncpg's 32767-param cap.

A long-history ticker (~5000 daily bars) × 11 columns = 55,000 params in one shot,
which previously raised asyncpg.InterfaceError. After the chunking fix the call
should succeed and persist every row.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from chronosync.db import repositories as repos


@pytest.mark.functional
@pytest.mark.requires_docker
@pytest.mark.asyncio
async def test_upsert_5000_rows(db_session) -> None:
    await repos.upsert_instruments(
        db_session,
        [
            {
                "ticker": "BIGHIST",
                "exchange": "NSE",
                "asset_class": "EQUITY",
                "country_code": "IN",
                "currency": "INR",
                "isin": None,
                "is_active": True,
            }
        ],
    )
    inst = await repos.get_instrument(db_session, "BIGHIST")
    assert inst is not None
    await db_session.commit()

    n = 5000
    start = date(2005, 1, 3)
    rows = [
        {
            "ts": start + timedelta(days=i),
            "open": Decimal("100"),
            "high": Decimal("101"),
            "low": Decimal("99"),
            "close": Decimal("100.5"),
            "volume": 1000,
            "vwap": None,
            "adj_factor": Decimal("1"),
            "open_interest": None,
        }
        for i in range(n)
    ]
    inserted = await repos.upsert_daily_bars(
        db_session,
        instrument_id=inst.id,
        feed_name="yfinance",
        rows=rows,
    )
    assert inserted == n
    await db_session.commit()

    persisted = await repos.daily_bars_range(
        db_session,
        instrument_id=inst.id,
        frm=start,
        to=start + timedelta(days=n),
        limit=n + 10,
    )
    assert len(persisted) == n
