"""Provider interface + DTOs. See docs/LLD.md §4.2, §5.1."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import ClassVar, Protocol, runtime_checkable

from chronosync.db.types import AssetClass


@dataclass(frozen=True, slots=True)
class BarRow:
    ts: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    vwap: Decimal | None
    adj_factor: Decimal
    open_interest: int | None = None


@dataclass(frozen=True, slots=True)
class InstrumentMeta:
    ticker: str
    exchange: str
    asset_class: AssetClass
    country_code: str
    currency: str
    isin: str | None
    market_cap: Decimal | None
    is_active: bool


@runtime_checkable
class BaseDataFeed(Protocol):
    name: ClassVar[str]
    supports_intraday: ClassVar[bool]
    supports_adjusted: ClassVar[bool]

    async def fetch_daily_bars(
        self,
        ticker: str,
        exchange: str,
        frm: date,
        to: date,
    ) -> AsyncIterator[BarRow]: ...

    async def fetch_instrument_meta(
        self,
        ticker: str,
        exchange: str,
    ) -> InstrumentMeta | None: ...

    async def close(self) -> None: ...
