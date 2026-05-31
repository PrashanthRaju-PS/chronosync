"""Pydantic response models — shared between server and client SDK."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict

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
    is_active: bool


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
