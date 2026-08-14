"""Narrow async CRUD helpers per aggregate. See docs/LLD.md §5.3."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from chronosync.db.models import (
    Account,
    DailyBar,
    Instrument,
    SyncState,
    User,
    VendorCredential,
)

# ---------- instruments ----------


async def list_active_instruments(
    session: AsyncSession,
    *,
    exchange: str | None = None,
) -> list[Instrument]:
    stmt = select(Instrument).where(Instrument.is_active.is_(True))
    if exchange:
        stmt = stmt.where(Instrument.exchange == exchange)
    stmt = stmt.order_by(Instrument.ticker)
    res = await session.execute(stmt)
    return list(res.scalars())


async def get_instrument(session: AsyncSession, id_or_ticker: str) -> Instrument | None:
    try:
        uid = UUID(id_or_ticker)
        stmt = select(Instrument).where(Instrument.id == uid)
    except ValueError:
        stmt = select(Instrument).where(Instrument.ticker == id_or_ticker)
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def find_instruments(
    session: AsyncSession,
    *,
    exchange: str | None = None,
    active: bool | None = None,
    fno: bool | None = None,
    q: str | None = None,
    after_ticker: str | None = None,
    limit: int = 100,
) -> list[Instrument]:
    stmt = select(Instrument)
    conds = []
    if exchange:
        conds.append(Instrument.exchange == exchange)
    if active is not None:
        conds.append(Instrument.is_active.is_(active))
    if fno is not None:
        conds.append(Instrument.is_fno.is_(fno))
    if q:
        conds.append(Instrument.ticker.ilike(f"{q}%"))
    if after_ticker:
        conds.append(Instrument.ticker > after_ticker)
    if conds:
        stmt = stmt.where(and_(*conds))
    stmt = stmt.order_by(Instrument.ticker).limit(limit)
    res = await session.execute(stmt)
    return list(res.scalars())


async def upsert_instruments(
    session: AsyncSession,
    rows: Sequence[dict[str, Any]],
) -> int:
    if not rows:
        return 0
    stmt = pg_insert(Instrument).values(list(rows))
    stmt = stmt.on_conflict_do_update(
        index_elements=["ticker", "exchange"],
        set_={
            "asset_class": stmt.excluded.asset_class,
            "country_code": stmt.excluded.country_code,
            "currency": stmt.excluded.currency,
            "isin": stmt.excluded.isin,
            "market_cap": stmt.excluded.market_cap,
            "market_cap_as_of": stmt.excluded.market_cap_as_of,
            "is_active": stmt.excluded.is_active,
            "updated_at": func.now(),
        },
    )
    res = await session.execute(stmt)
    return res.rowcount or 0


async def deactivate_missing(
    session: AsyncSession,
    *,
    exchange: str,
    seen_tickers: Sequence[str],
) -> int:
    stmt = (
        update(Instrument)
        .where(Instrument.exchange == exchange, Instrument.ticker.notin_(seen_tickers))
        .values(is_active=False, updated_at=func.now())
    )
    res = await session.execute(stmt)
    return res.rowcount or 0


async def update_instrument_market_cap(
    session: AsyncSession,
    *,
    instrument_id: UUID,
    market_cap: Decimal | None,
    as_of: date,
) -> int:
    """Refresh market_cap snapshot. No-op (returns 0) when value is None so
    transient provider misses never clobber an existing good value."""
    if market_cap is None:
        return 0
    stmt = (
        update(Instrument)
        .where(Instrument.id == instrument_id)
        .values(market_cap=market_cap, market_cap_as_of=as_of, updated_at=func.now())
    )
    res = await session.execute(stmt)
    return res.rowcount or 0


async def set_fno_flags(
    session: AsyncSession,
    *,
    exchange: str,
    fno_tickers: Sequence[str],
    as_of: date,
) -> tuple[int, int]:
    """Reconcile the F&O membership flag for one exchange against `fno_tickers`.

    Marks every listed ticker `is_fno=True` and everything else on the exchange
    `is_fno=False`, stamping `fno_as_of=as_of` on the whole exchange so the last
    refresh is always datable (even the rows that stayed False). Returns
    ``(marked_true, cleared_false)``.

    A membership set that comes back empty is treated as a provider miss and
    skipped — the exchange list is never legitimately empty, so clearing every
    flag on an empty fetch would silently wipe the universe.
    """
    wanted = {t.strip().upper() for t in fno_tickers if t and t.strip()}
    if not wanted:
        return (0, 0)
    on = (
        update(Instrument)
        .where(Instrument.exchange == exchange, func.upper(Instrument.ticker).in_(wanted))
        .values(is_fno=True, fno_as_of=as_of, updated_at=func.now())
    )
    off = (
        update(Instrument)
        .where(Instrument.exchange == exchange, func.upper(Instrument.ticker).notin_(wanted))
        .values(is_fno=False, fno_as_of=as_of, updated_at=func.now())
    )
    marked = (await session.execute(on)).rowcount or 0
    cleared = (await session.execute(off)).rowcount or 0
    return (marked, cleared)


async def fno_watermark(session: AsyncSession, *, exchange: str | None = None) -> date | None:
    """Newest `fno_as_of` across instruments — the last time the F&O flag was
    refreshed. None when it has never run. Drives the monthly staleness guard."""
    stmt = select(func.max(Instrument.fno_as_of))
    if exchange:
        stmt = stmt.where(Instrument.exchange == exchange)
    return (await session.execute(stmt)).scalar_one_or_none()


async def market_cap_watermark(
    session: AsyncSession, *, exchange: str | None = None
) -> date | None:
    """Newest `market_cap_as_of` — the last time any market cap was refreshed.
    Drives the meta staleness catch-up (heals a missed weekly refresh)."""
    stmt = select(func.max(Instrument.market_cap_as_of))
    if exchange:
        stmt = stmt.where(Instrument.exchange == exchange)
    return (await session.execute(stmt)).scalar_one_or_none()


# ---------- daily bars ----------


# Postgres wire-protocol caps prepared-statement parameters at 65535;
# asyncpg's safe ceiling is 32767. DailyBar upsert binds 11 cols per row,
# so we chunk to stay well under: 2000 rows × 11 = 22000 params per round-trip.
_UPSERT_DAILY_CHUNK = 2000


async def upsert_daily_bars(
    session: AsyncSession,
    *,
    instrument_id: UUID,
    feed_name: str,
    rows: Sequence[dict[str, Any]],
) -> int:
    if not rows:
        return 0
    total = 0
    for start in range(0, len(rows), _UPSERT_DAILY_CHUNK):
        chunk = rows[start : start + _UPSERT_DAILY_CHUNK]
        payload = [{"instrument_id": instrument_id, "feed_name": feed_name, **r} for r in chunk]
        stmt = pg_insert(DailyBar).values(payload)
        stmt = stmt.on_conflict_do_update(
            index_elements=["instrument_id", "ts"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
                "vwap": stmt.excluded.vwap,
                "adj_factor": stmt.excluded.adj_factor,
                "open_interest": stmt.excluded.open_interest,
                "feed_name": stmt.excluded.feed_name,
            },
        )
        res = await session.execute(stmt)
        total += res.rowcount or 0
    return total


async def daily_bars_range(
    session: AsyncSession,
    *,
    instrument_id: UUID,
    frm: date,
    to: date,
    after_ts: date | None = None,
    limit: int = 1000,
) -> list[DailyBar]:
    stmt = select(DailyBar).where(
        DailyBar.instrument_id == instrument_id,
        DailyBar.ts >= frm,
        DailyBar.ts <= to,
    )
    if after_ts:
        stmt = stmt.where(DailyBar.ts > after_ts)
    stmt = stmt.order_by(DailyBar.ts).limit(limit)
    res = await session.execute(stmt)
    return list(res.scalars())


async def max_synced_ts(session: AsyncSession, instrument_id: UUID) -> date | None:
    stmt = select(func.max(DailyBar.ts)).where(DailyBar.instrument_id == instrument_id)
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


# ---------- sync state ----------


async def last_synced_map(
    session: AsyncSession,
    *,
    feed_name: str,
    instrument_ids: Sequence[UUID] | None = None,
) -> dict[UUID, date | None]:
    stmt = select(SyncState.instrument_id, SyncState.last_synced_ts).where(
        SyncState.feed_name == feed_name
    )
    if instrument_ids is not None:
        stmt = stmt.where(SyncState.instrument_id.in_(instrument_ids))
    res = await session.execute(stmt)
    return {row.instrument_id: row.last_synced_ts for row in res}


async def mark_sync_started(
    session: AsyncSession,
    *,
    instrument_id: UUID,
    feed_name: str,
) -> None:
    now = datetime.now(UTC)
    stmt = pg_insert(SyncState).values(
        instrument_id=instrument_id,
        feed_name=feed_name,
        last_run_started_at=now,
        attempt_count=1,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["instrument_id", "feed_name"],
        set_={
            "last_run_started_at": now,
            "attempt_count": SyncState.attempt_count + 1,
        },
    )
    await session.execute(stmt)


async def mark_sync_success(
    session: AsyncSession,
    *,
    instrument_id: UUID,
    feed_name: str,
    last_ts: date,
) -> None:
    now = datetime.now(UTC)
    stmt = pg_insert(SyncState).values(
        instrument_id=instrument_id,
        feed_name=feed_name,
        last_synced_ts=last_ts,
        last_run_finished_at=now,
        last_error=None,
    )
    # Backfill chunks land out-of-order under asyncio.gather; keep the highest
    # last_synced_ts so the column is monotonic and incremental planning is correct.
    stmt = stmt.on_conflict_do_update(
        index_elements=["instrument_id", "feed_name"],
        set_={
            "last_synced_ts": func.greatest(SyncState.last_synced_ts, stmt.excluded.last_synced_ts),
            "last_run_finished_at": now,
            "last_error": None,
        },
    )
    await session.execute(stmt)


async def mark_sync_finished(
    session: AsyncSession,
    *,
    instrument_id: UUID,
    feed_name: str,
) -> None:
    """Close a run that fetched no new bars: stamp last_run_finished_at and clear
    last_error, without touching last_synced_ts. Without this, the empty-result
    path leaves last_run_started_at ahead of last_run_finished_at forever, so the
    row reads as perpetually 'in progress'. The row must already exist (started)."""
    now = datetime.now(UTC)
    stmt = (
        update(SyncState)
        .where(
            SyncState.instrument_id == instrument_id,
            SyncState.feed_name == feed_name,
        )
        .values(last_run_finished_at=now, last_error=None)
    )
    await session.execute(stmt)


async def mark_sync_error(
    session: AsyncSession,
    *,
    instrument_id: UUID,
    feed_name: str,
    error: str,
) -> None:
    now = datetime.now(UTC)
    stmt = pg_insert(SyncState).values(
        instrument_id=instrument_id,
        feed_name=feed_name,
        last_run_finished_at=now,
        last_error=error[:2000],
        attempt_count=1,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["instrument_id", "feed_name"],
        set_={
            "last_run_finished_at": now,
            "last_error": error[:2000],
        },
    )
    await session.execute(stmt)


async def sync_status_for(
    session: AsyncSession,
    *,
    instrument_id: UUID | None = None,
    feed_name: str | None = None,
    limit: int = 100,
) -> list[SyncState]:
    stmt = select(SyncState)
    if instrument_id:
        stmt = stmt.where(SyncState.instrument_id == instrument_id)
    if feed_name:
        stmt = stmt.where(SyncState.feed_name == feed_name)
    stmt = stmt.limit(limit)
    res = await session.execute(stmt)
    return list(res.scalars())


async def last_global_sync_at(session: AsyncSession) -> datetime | None:
    stmt = select(func.max(SyncState.last_run_finished_at))
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def last_synced_max(session: AsyncSession, *, feed_name: str | None = None) -> date | None:
    """Newest bar date present across feeds — the data-freshness watermark
    (distinct from last_global_sync_at, which is when a run last *finished*)."""
    stmt = select(func.max(SyncState.last_synced_ts))
    if feed_name:
        stmt = stmt.where(SyncState.feed_name == feed_name)
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


# ---------- users / accounts / credentials ----------


async def add_user(session: AsyncSession, *, email: str, display_name: str | None) -> User:
    user = User(email=email, display_name=display_name)
    session.add(user)
    await session.flush()
    return user


async def list_users(session: AsyncSession) -> list[User]:
    res = await session.execute(select(User).order_by(User.email))
    return list(res.scalars())


async def disable_user(session: AsyncSession, user_id: UUID) -> None:
    await session.execute(
        update(User).where(User.id == user_id).values(is_active=False)
    )


async def add_account(
    session: AsyncSession,
    *,
    user_id: UUID,
    vendor: str,
    label: str,
) -> Account:
    acct = Account(user_id=user_id, vendor=vendor, account_label=label)
    session.add(acct)
    await session.flush()
    return acct


async def list_accounts(session: AsyncSession, *, user_id: UUID | None = None) -> list[Account]:
    stmt = select(Account)
    if user_id:
        stmt = stmt.where(Account.user_id == user_id)
    stmt = stmt.order_by(Account.vendor, Account.account_label)
    res = await session.execute(stmt)
    return list(res.scalars())


async def remove_account(session: AsyncSession, account_id: UUID) -> None:
    from sqlalchemy import delete

    await session.execute(delete(Account).where(Account.id == account_id))


async def upsert_credential(
    session: AsyncSession,
    *,
    account_id: UUID,
    key_name: str,
    secret_ref: str,
    rotated: bool = False,
) -> None:
    now = datetime.now(UTC)
    values: dict[str, Any] = {
        "account_id": account_id,
        "key_name": key_name,
        "secret_ref": secret_ref,
    }
    if rotated:
        values["rotated_at"] = now
    stmt = pg_insert(VendorCredential).values(**values)
    set_: dict[str, Any] = {"secret_ref": stmt.excluded.secret_ref}
    if rotated:
        set_["rotated_at"] = now
    stmt = stmt.on_conflict_do_update(index_elements=["account_id", "key_name"], set_=set_)
    await session.execute(stmt)


async def get_credential(
    session: AsyncSession,
    *,
    account_id: UUID,
    key_name: str,
) -> VendorCredential | None:
    stmt = select(VendorCredential).where(
        VendorCredential.account_id == account_id,
        VendorCredential.key_name == key_name,
    )
    res = await session.execute(stmt)
    return res.scalar_one_or_none()


async def delete_credential(
    session: AsyncSession,
    *,
    account_id: UUID,
    key_name: str,
) -> None:
    from sqlalchemy import delete

    await session.execute(
        delete(VendorCredential).where(
            VendorCredential.account_id == account_id,
            VendorCredential.key_name == key_name,
        )
    )


# ---------- misc helpers ----------


def decimal_or_none(v: Any) -> Decimal | None:
    if v is None:
        return None
    return Decimal(str(v))


__all__ = [
    "add_account",
    "add_user",
    "daily_bars_range",
    "deactivate_missing",
    "decimal_or_none",
    "delete_credential",
    "disable_user",
    "find_instruments",
    "get_credential",
    "get_instrument",
    "last_global_sync_at",
    "last_synced_map",
    "list_accounts",
    "list_active_instruments",
    "list_users",
    "mark_sync_error",
    "mark_sync_started",
    "mark_sync_success",
    "max_synced_ts",
    "remove_account",
    "sync_status_for",
    "update_instrument_market_cap",
    "upsert_credential",
    "upsert_daily_bars",
    "upsert_instruments",
]


# kept to satisfy ruff "F401" sweep for SQL helpers used elsewhere
_ = or_  # noqa: F841
