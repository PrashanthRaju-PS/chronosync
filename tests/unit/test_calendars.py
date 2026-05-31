"""Calendar wrapper sanity checks (depends on pandas_market_calendars datasets)."""

from __future__ import annotations

from datetime import date

import pytest

from chronosync.calendars import is_trading_day, next_trading_day, prev_trading_day, trading_days


@pytest.mark.unit
def test_weekend_skipped_nse() -> None:
    # 2024-01-06 is a Saturday in IST.
    saturday = date(2024, 1, 6)
    assert is_trading_day("NSE", saturday) is False


@pytest.mark.unit
def test_known_weekday_open_nse() -> None:
    # 2024-01-04 is a Thursday — should be open unless a holiday.
    thursday = date(2024, 1, 4)
    assert is_trading_day("NSE", thursday) is True


@pytest.mark.unit
def test_trading_days_range() -> None:
    days = trading_days("NSE", date(2024, 1, 1), date(2024, 1, 12))
    assert all(d.weekday() < 5 for d in days)
    assert len(days) >= 5  # at least one full week minus a known holiday


@pytest.mark.unit
def test_next_prev_trading_day() -> None:
    sat = date(2024, 1, 6)
    nxt = next_trading_day("NSE", sat)
    prv = prev_trading_day("NSE", sat)
    assert nxt.weekday() < 5 and nxt > sat
    assert prv.weekday() < 5 and prv < sat


@pytest.mark.unit
def test_empty_range_returns_empty() -> None:
    assert trading_days("NSE", date(2024, 2, 1), date(2024, 1, 1)) == []
