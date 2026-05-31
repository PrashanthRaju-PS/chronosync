"""Trading calendar wrapper over pandas_market_calendars."""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

import pandas as pd
import pandas_market_calendars as mcal

_EXCHANGE_ALIASES = {
    "NSE": "NSE",
    "BSE": "BSE",
    "NYSE": "NYSE",
    "NASDAQ": "NASDAQ",
}


@lru_cache(maxsize=16)
def _calendar(exchange: str) -> mcal.MarketCalendar:
    alias = _EXCHANGE_ALIASES.get(exchange.upper(), exchange.upper())
    return mcal.get_calendar(alias)


def trading_days(exchange: str, frm: date, to: date) -> list[date]:
    if frm > to:
        return []
    cal = _calendar(exchange)
    sched = cal.schedule(start_date=frm.isoformat(), end_date=to.isoformat())
    if sched.empty:
        return []
    return [d.date() for d in pd.to_datetime(sched.index)]


def is_trading_day(exchange: str, day: date) -> bool:
    return day in trading_days(exchange, day, day)


def next_trading_day(exchange: str, day: date) -> date:
    candidate = day + timedelta(days=1)
    for _ in range(14):
        if is_trading_day(exchange, candidate):
            return candidate
        candidate += timedelta(days=1)
    return candidate


def prev_trading_day(exchange: str, day: date) -> date:
    candidate = day - timedelta(days=1)
    for _ in range(14):
        if is_trading_day(exchange, candidate):
            return candidate
        candidate -= timedelta(days=1)
    return candidate
