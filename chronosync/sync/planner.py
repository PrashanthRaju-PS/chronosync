"""Compute per-instrument fetch tasks. See docs/LLD.md §4.3, §6.2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from chronosync.calendars import trading_days
from chronosync.db import repositories as repos


@dataclass(frozen=True, slots=True)
class FetchTask:
    instrument_id: UUID
    ticker: str
    exchange: str
    feed_name: str
    from_date: date
    to_date: date


async def build_tasks(
    session: AsyncSession,
    *,
    exchange: str,
    feed_name: str,
    today: date | None = None,
    default_lookback_days: int = 365,
) -> list[FetchTask]:
    today = today or date.today()
    instruments = await repos.list_active_instruments(session, exchange=exchange)
    if not instruments:
        return []
    last_synced = await repos.last_synced_map(
        session,
        feed_name=feed_name,
        instrument_ids=[i.id for i in instruments],
    )

    tasks: list[FetchTask] = []
    for inst in instruments:
        last_ts = last_synced.get(inst.id)
        frm = last_ts + timedelta(days=1) if last_ts else today - timedelta(days=default_lookback_days)
        if frm > today:
            continue
        if not trading_days(exchange, frm, today):
            continue
        tasks.append(
            FetchTask(
                instrument_id=inst.id,
                ticker=inst.ticker,
                exchange=inst.exchange,
                feed_name=feed_name,
                from_date=frm,
                to_date=today,
            )
        )
    return tasks


def split_backfill(
    instrument_id: UUID,
    ticker: str,
    exchange: str,
    feed_name: str,
    frm: date,
    to: date,
    *,
    batch_days: int,
) -> list[FetchTask]:
    """Slice a long backfill window into N-day chunks so a failure rewinds at most one chunk."""
    if frm > to:
        return []
    out: list[FetchTask] = []
    cursor = frm
    while cursor <= to:
        end = min(cursor + timedelta(days=batch_days - 1), to)
        out.append(
            FetchTask(
                instrument_id=instrument_id,
                ticker=ticker,
                exchange=exchange,
                feed_name=feed_name,
                from_date=cursor,
                to_date=end,
            )
        )
        cursor = end + timedelta(days=1)
    return out
