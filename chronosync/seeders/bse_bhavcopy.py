"""BSE Bhavcopy seeder."""

from __future__ import annotations

import asyncio
import csv
import io
import zipfile
from datetime import date, timedelta
from typing import Any, ClassVar

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import (
    AsyncRetrying,
    RetryError,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from chronosync.calendars import prev_trading_day
from chronosync.db.repositories import deactivate_missing, upsert_instruments
from chronosync.db.types import AssetClass
from chronosync.exceptions import BhavcopyFetchError
from chronosync.logging import get_logger
from chronosync.seeders._urls import bse_url

_log = get_logger(__name__)

_USER_AGENT = "Mozilla/5.0 ChronoSync/0.1"


class BSEBhavcopySeeder:
    exchange: ClassVar[str] = "BSE"

    def __init__(self, *, on_date: date | None = None) -> None:
        self._on_date = on_date

    async def run(self, session: AsyncSession) -> int:
        target = self._on_date or prev_trading_day(self.exchange, date.today() + timedelta(days=1))
        url = bse_url(target)
        _log.info("bse_bhavcopy_fetch_start", url=url, on_date=target.isoformat())

        retry = AsyncRetrying(
            stop=stop_after_attempt(5),
            wait=wait_exponential(multiplier=1, max=30),
            retry=retry_if_exception_type(BhavcopyFetchError),
            reraise=True,
        )
        try:
            content = await self._with_retry(retry, url)
        except RetryError as e:
            raise BhavcopyFetchError(str(e)) from e

        rows = await asyncio.to_thread(_parse_zip_csv, content)
        if not rows:
            raise BhavcopyFetchError(f"empty BSE bhavcopy at {url}")

        seen_tickers = [r["ticker"] for r in rows]
        touched = await upsert_instruments(session, rows)
        deactivated = await deactivate_missing(
            session,
            exchange=self.exchange,
            seen_tickers=seen_tickers,
        )
        _log.info(
            "bse_bhavcopy_done",
            upserted=touched,
            deactivated=deactivated,
            unique=len(seen_tickers),
        )
        return touched

    async def _with_retry(self, retry: AsyncRetrying, url: str) -> bytes:
        async for attempt in retry:
            with attempt:
                return await self._fetch(url)
        raise BhavcopyFetchError("retry exhausted")  # pragma: no cover

    async def _fetch(self, url: str) -> bytes:
        try:
            async with httpx.AsyncClient(
                timeout=30, headers={"User-Agent": _USER_AGENT}, follow_redirects=True
            ) as client:
                r = await client.get(url)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise BhavcopyFetchError(f"HTTP {r.status_code}")
                r.raise_for_status()
                return r.content
        except httpx.HTTPError as e:
            raise BhavcopyFetchError(str(e)) from e


def _parse_zip_csv(content: bytes) -> list[dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        csv_name = next((n for n in zf.namelist() if n.lower().endswith(".csv")), None)
        if csv_name is None:
            raise BhavcopyFetchError("no CSV in BSE bhavcopy ZIP")
        with zf.open(csv_name) as fp:
            text = io.TextIOWrapper(fp, encoding="utf-8")
            reader = csv.DictReader(text)
            rows: list[dict[str, Any]] = []
            for r in reader:
                parsed = _row_to_instrument(r)
                if parsed is not None:
                    rows.append(parsed)
            return rows


def _row_to_instrument(r: dict[str, str]) -> dict[str, Any] | None:
    code = (r.get("SC_CODE") or r.get("SCRIP_CD") or "").strip()
    ticker = (r.get("SC_NAME") or r.get("SCRIP_NAME") or "").strip().upper()
    if not code or not ticker:
        return None
    isin = (r.get("ISIN_CODE") or r.get("ISIN") or "").strip() or None
    return {
        "ticker": code,  # BSE numeric scrip code is unique
        "exchange": "BSE",
        "asset_class": AssetClass.EQUITY.value,
        "country_code": "IN",
        "currency": "INR",
        "isin": isin,
        "is_active": True,
    }
