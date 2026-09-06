"""Monthly refresh of instruments.is_fno (F&O / derivatives membership).

Unlike market_cap (a per-ticker snapshot from the price feed), F&O membership is
a single small list published by the exchange: the set of underlyings that have
listed futures & options. We fetch that list once and reconcile the flag across
the whole exchange — marking listed underlyings True and everything else False —
so both additions and the semi-annual removals are reflected.

Source: NSE's derivatives "underlying information" JSON API. It sits behind the
same soft bot-wall as the rest of nseindia.com, so we prime cookies against the
site root first (same User-Agent trick the Bhavcopy seeder uses). URL overridable
via CHRONOSYNC_SEEDER_NSE_FNO_URL for when NSE reshuffles paths.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import date

import httpx
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from chronosync.db import engine as db_engine
from chronosync.db import repositories as repos
from chronosync.exceptions import ProviderError
from chronosync.logging import get_logger

_log = get_logger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Cookie-priming pages hit before the JSON API (NSE 403s an API call that arrives
# without a session cookie from the site itself).
NSE_FNO_WARMUP_URLS: tuple[str, ...] = (
    "https://www.nseindia.com/",
    "https://www.nseindia.com/market-data/securities-available-for-trading",
)
NSE_FNO_URL_DEFAULT = "https://www.nseindia.com/api/underlying-information"


def _nse_fno_url() -> str:
    return os.environ.get("CHRONOSYNC_SEEDER_NSE_FNO_URL", NSE_FNO_URL_DEFAULT)


@dataclass(slots=True)
class FnoRefreshSummary:
    exchange: str
    fetched: int = 0      # symbols returned by the source
    marked: int = 0       # rows set is_fno=True
    cleared: int = 0      # rows set is_fno=False
    duration_s: float = 0.0


def _parse_underlying_symbols(payload: dict) -> list[str]:
    """Stock underlyings from an underlying-information response. Indices
    (NIFTY, BANKNIFTY, …) live under IndexList and are intentionally excluded —
    they are not tradable equities in the instruments table."""
    data = payload.get("data") or payload
    out: list[str] = []
    for row in data.get("UnderlyingList") or []:
        sym = (row.get("symbol") or "").strip().upper()
        if sym:
            out.append(sym)
    return out


async def fetch_nse_fno_symbols(client: httpx.AsyncClient | None = None) -> list[str]:
    """Fetch the NSE F&O underlying stock symbols. Raises ProviderError on
    failure or an empty/garbled response (an empty list is never legitimate and
    must not be allowed to clear the flag downstream)."""
    owns = client is None
    c = client or httpx.AsyncClient(
        timeout=30, headers={"User-Agent": _USER_AGENT, "Accept": "application/json,*/*"},
        follow_redirects=True,
    )
    try:
        for warm in NSE_FNO_WARMUP_URLS:
            try:
                await c.get(warm)
            except httpx.HTTPError:
                pass  # priming is best-effort; the API call below is what matters
        try:
            r = await c.get(_nse_fno_url())
            r.raise_for_status()
            payload = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ProviderError(f"NSE F&O fetch failed: {e}") from e
        symbols = _parse_underlying_symbols(payload)
        if not symbols:
            raise ProviderError("NSE F&O list came back empty")
        return symbols
    finally:
        if owns:
            await c.aclose()


async def run(*, exchange: str = "NSE", today: date | None = None) -> FnoRefreshSummary:
    """Fetch the F&O list and reconcile instruments.is_fno for one exchange.

    Retries the network fetch with backoff; a persistent failure leaves the
    existing flags untouched (returns a zeroed summary) rather than wiping them.
    """
    summary = FnoRefreshSummary(exchange=exchange)
    started = time.perf_counter()
    as_of = today or date.today()

    retry = AsyncRetrying(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, max=30),
        retry=retry_if_exception_type(ProviderError),
        reraise=True,
    )
    try:
        symbols: list[str] = []
        async for attempt in retry:
            with attempt:
                symbols = await fetch_nse_fno_symbols()
    except (RetryError, ProviderError) as e:
        _log.warning("fno_refresh_fetch_failed", exchange=exchange, err=str(e))
        summary.duration_s = round(time.perf_counter() - started, 3)
        return summary

    summary.fetched = len(symbols)
    async with db_engine.session_scope() as s:
        marked, cleared = await repos.set_fno_flags(
            s, exchange=exchange, fno_tickers=symbols, as_of=as_of
        )
    summary.marked, summary.cleared = marked, cleared
    summary.duration_s = round(time.perf_counter() - started, 3)
    return summary
