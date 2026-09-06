"""SQLAlchemy 2.x models. Mirrors the DDL in docs/LLD.md §2."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM as PG_ENUM
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from chronosync.db.types import AssetClass


class Base(DeclarativeBase):
    pass


asset_class_enum = PG_ENUM(
    AssetClass,
    name="asset_class",
    create_type=False,
    values_callable=lambda cls: [e.value for e in cls],
)


class Instrument(Base):
    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint("ticker", "exchange", name="uq_instruments_ticker_exchange"),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    ticker: Mapped[str] = mapped_column(String, nullable=False)
    exchange: Mapped[str] = mapped_column(String, nullable=False)
    asset_class: Mapped[AssetClass] = mapped_column(
        asset_class_enum, nullable=False, default=AssetClass.EQUITY
    )
    country_code: Mapped[str] = mapped_column(CHAR(2), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    isin: Mapped[str | None] = mapped_column(String, nullable=True)
    market_cap: Mapped[Decimal | None] = mapped_column(Numeric(20, 2), nullable=True)
    market_cap_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    # F&O (derivatives) membership: whether this underlying has listed futures/options.
    # Populated monthly from the exchange's derivatives underlying list, not the price
    # feed — see chronosync/sync/fno_refresh.py. fno_as_of stamps the last refresh so a
    # missed run can be detected (mirrors market_cap_as_of).
    is_fno: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    fno_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class DailyBar(Base):
    __tablename__ = "daily_bars"

    instrument_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("instruments.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ts: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    vwap: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    adj_factor: Mapped[Decimal] = mapped_column(
        Numeric(20, 10), nullable=False, default=Decimal("1")
    )
    open_interest: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    feed_name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class IntradayBar(Base):
    __tablename__ = "intraday_bars"

    instrument_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("instruments.id", ondelete="CASCADE"),
        primary_key=True,
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    interval: Mapped[str] = mapped_column(String, primary_key=True)
    open: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(20, 6), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)
    vwap: Mapped[Decimal | None] = mapped_column(Numeric(20, 6), nullable=True)
    feed_name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class SyncState(Base):
    __tablename__ = "sync_state"

    instrument_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("instruments.id", ondelete="CASCADE"),
        primary_key=True,
    )
    feed_name: Mapped[str] = mapped_column(String, primary_key=True)
    last_synced_ts: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_run_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_run_finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class User(Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "vendor", "account_label", name="uq_accounts_user_vendor_label"
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    vendor: Mapped[str] = mapped_column(String, nullable=False)
    account_label: Mapped[str] = mapped_column(String, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class VendorCredential(Base):
    __tablename__ = "vendor_credentials"

    account_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        primary_key=True,
    )
    key_name: Mapped[str] = mapped_column(String, primary_key=True)
    secret_ref: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
