"""yfinance-backed BaseDataFeed.

yfinance is synchronous and blocking — we wrap calls in asyncio.to_thread.
Exchange suffix mapping: NSE → '.NS', BSE → '.BO'.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, ClassVar

import yfinance as yf
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from chronosync.db.types import AssetClass
from chronosync.exceptions import PermanentProviderError, RetriableProviderError
from chronosync.logging import get_logger
from chronosync.providers.base import BarRow, InstrumentMeta

_log = get_logger(__name__)

_EXCHANGE_SUFFIX = {
    "NSE": ".NS",
    "BSE": ".BO",
    "NYSE": "",
    "NASDAQ": "",
}


def _yf_symbol(ticker: str, exchange: str) -> str:
    suffix = _EXCHANGE_SUFFIX.get(exchange.upper())
    if suffix is None:
        raise PermanentProviderError(f"yfinance does not map exchange {exchange!r}")
    return f"{ticker}{suffix}"


class YFinanceFeed:
    name: ClassVar[str] = "yfinance"
    supports_intraday: ClassVar[bool] = True
    supports_adjusted: ClassVar[bool] = True

    def __init__(
        self,
        *,
        concurrency: int = 8,
        retry_max_attempts: int = 5,
        retry_base_delay_s: float = 1.0,
        retry_max_delay_s: float = 30.0,
    ) -> None:
        self._sem = asyncio.Semaphore(concurrency)
        self._retry = AsyncRetrying(
            stop=stop_after_attempt(retry_max_attempts),
            wait=wait_exponential(multiplier=retry_base_delay_s, max=retry_max_delay_s),
            retry=retry_if_exception_type(RetriableProviderError),
            reraise=True,
        )

    async def fetch_daily_bars(
        self,
        ticker: str,
        exchange: str,
        frm: date,
        to: date,
    ) -> AsyncIterator[BarRow]:
        symbol = _yf_symbol(ticker, exchange)
        async with self._sem:
            try:
                df = await self._download_with_retry(symbol, frm, to)
            except RetryError as e:
                raise RetriableProviderError(str(e)) from e

        for row in _df_to_bars(df):
            yield row

    async def _download_with_retry(self, symbol: str, frm: date, to: date) -> Any:
        async for attempt in self._retry:
            with attempt:
                return await asyncio.to_thread(_yf_download_sync, symbol, frm, to)
        raise RetriableProviderError(f"exhausted retries for {symbol}")  # pragma: no cover

    async def fetch_instrument_meta(self, ticker: str, exchange: str) -> InstrumentMeta | None:
        """Market-cap snapshot for one instrument.

        Sources market_cap from yfinance `fast_info` (the lightweight v8 quote
        path), falling back to the heavier `.info` quoteSummary only if that
        misses. `.info` gets throttled to an empty dict at whole-universe scale
        — which is why the weekly refresh silently populated nothing — whereas
        fast_info holds up. The meta-refresh consumer persists only market_cap,
        so the other InstrumentMeta fields keep their equity defaults.
        """
        symbol = _yf_symbol(ticker, exchange)
        async with self._sem:
            mc = await asyncio.to_thread(_yf_market_cap_sync, symbol)
        if mc is None:
            return None
        return InstrumentMeta(
            ticker=ticker,
            exchange=exchange,
            asset_class=AssetClass.EQUITY,
            country_code="IN",
            currency="INR",
            isin=None,
            market_cap=Decimal(str(mc)).quantize(Decimal("0.01")),
            is_active=True,
        )

    async def close(self) -> None:
        return None


def _yf_download_sync(symbol: str, frm: date, to: date) -> Any:
    # auto_adjust=False keeps both raw OHLC and Adj Close so we can store adj_factor.
    end = to + timedelta(days=1)  # yfinance end is exclusive
    try:
        df = yf.download(
            symbol,
            start=frm.isoformat(),
            end=end.isoformat(),
            auto_adjust=False,
            actions=False,
            progress=False,
            threads=False,
        )
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if "rate" in msg or "429" in msg or "timeout" in msg or "connection" in msg:
            raise RetriableProviderError(str(e)) from e
        raise PermanentProviderError(str(e)) from e
    if df is None or df.empty:
        return df
    if hasattr(df.columns, "get_level_values"):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    return df


_RATE_LIMIT_HINTS = ("too many requests", "rate limit", "429")


def _is_rate_limited(msg: str) -> bool:
    m = msg.lower()
    return any(h in m for h in _RATE_LIMIT_HINTS)


def _yf_market_cap_sync(symbol: str, *, attempts: int = 5) -> float | None:
    """market_cap via fast_info first, then .info, with backoff-retry on Yahoo
    rate-limiting.

    At whole-universe scale Yahoo starts returning "Too Many Requests"; fast_info
    swallows that into a None, so a throttled real stock is indistinguishable from
    a genuine no-market-cap ETF *unless* we probe .info, which raises the 429. On
    a rate-limit signal we back off and retry; a clean miss (no rate-limit) returns
    None immediately (ETFs / fund units legitimately have no market cap)."""
    for i in range(attempts):
        try:
            mc = getattr(yf.Ticker(symbol).fast_info, "market_cap", None)
            if mc:
                return float(mc)
        except Exception as e:  # noqa: BLE001
            if _is_rate_limited(str(e)) and i < attempts - 1:
                time.sleep(_backoff(i))
                continue
        # fast_info missed — confirm via .info, which surfaces the 429 explicitly.
        try:
            info = dict(yf.Ticker(symbol).info or {})
        except Exception as e:  # noqa: BLE001
            if _is_rate_limited(str(e)) and i < attempts - 1:
                time.sleep(_backoff(i))
                continue
            _log.warning("yfinance_info_failed", symbol=symbol, err=str(e))
            return None
        mc = info.get("marketCap")
        return float(mc) if mc else None
    return None


def _backoff(attempt: int) -> float:
    return min(2.0**attempt, 30.0) + random.uniform(0.0, 1.0)


def _yf_info_sync(symbol: str) -> dict[str, Any]:
    try:
        t = yf.Ticker(symbol)
        return dict(t.info or {})
    except Exception as e:  # noqa: BLE001
        _log.warning("yfinance_info_failed", symbol=symbol, err=str(e))
        return {}


def _df_to_bars(df: Any) -> list[BarRow]:
    if df is None or getattr(df, "empty", True):
        return []
    rows: list[BarRow] = []
    for ts, r in df.iterrows():
        try:
            # Drop price-less rows. yfinance intermittently emits a row carrying a
            # volume but NaN OHLC (a provider glitch, or a session it hasn't
            # settled yet). Decimal(str(nan)) yields Decimal("NaN") instead of
            # raising, so the except below never sees it and the NaN would persist
            # — poisoning every consumer (the read API 500s on a non-finite close,
            # and indicators silently go NaN). Volume alone is not a bar.
            if any(_is_nan(r.get(k)) for k in ("Open", "High", "Low", "Close")):
                continue
            o = Decimal(str(r["Open"]))
            h = Decimal(str(r["High"]))
            low = Decimal(str(r["Low"]))
            c = Decimal(str(r["Close"]))
            vol = int(r["Volume"]) if not _is_nan(r["Volume"]) else 0
            adj_close = r.get("Adj Close")
            if adj_close is None or _is_nan(adj_close) or c == 0:
                adj = Decimal("1")
            else:
                adj = (Decimal(str(adj_close)) / c).quantize(Decimal("0.0000000001"))
        except (KeyError, ValueError):
            continue
        d = ts.date() if hasattr(ts, "date") else ts
        rows.append(
            BarRow(
                ts=d,
                open=o,
                high=h,
                low=low,
                close=c,
                volume=vol,
                vwap=None,
                adj_factor=adj,
                open_interest=None,
            )
        )
    return rows


def _is_nan(v: Any) -> bool:
    try:
        return v != v  # NaN != NaN
    except TypeError:
        return False
