"""Pydantic response models — shared between server and client SDK."""

from __future__ import annotations

import math
from datetime import date, datetime
from decimal import Decimal
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, field_validator

T = TypeVar("T")


class InstrumentDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    ticker: str
    exchange: str
    asset_class: str
    country_code: str
    currency: str
    isin: str | None = None
    market_cap: Decimal | None = None
    market_cap_as_of: date | None = None
    is_fno: bool = False
    fno_as_of: date | None = None
    is_active: bool

    @field_validator("market_cap", mode="before")
    @classmethod
    def _drop_non_finite_market_cap(cls, v: object) -> object:
        # A NaN/inf market cap (a bad provider snapshot) is a valid Decimal/NUMERIC
        # but pydantic's Decimal type rejects non-finite values — so a single bad
        # row would raise on model_validate and 500 the entire /instruments list.
        # Runs `before` core validation and coerces it to null, so one bad row
        # degrades to a missing field rather than an outage.
        if isinstance(v, Decimal) and not v.is_finite():
            return None
        if isinstance(v, float) and not math.isfinite(v):
            return None
        return v


class DailyBarDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    instrument_id: UUID
    ts: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adj_close: Decimal | None = None
    volume: int
    vwap: Decimal | None = None
    adj_factor: Decimal
    open_interest: int | None = None
    feed_name: str


class SyncStatusDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    instrument_id: UUID
    feed_name: str
    last_synced_ts: date | None = None
    last_run_started_at: datetime | None = None
    last_run_finished_at: datetime | None = None
    last_error: str | None = None
    attempt_count: int


class HealthDTO(BaseModel):
    status: str
    db_ok: bool
    last_sync_at: datetime | None = None


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None
