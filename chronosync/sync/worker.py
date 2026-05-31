"""Bounded-concurrency fetch + upsert worker. See docs/LLD.md §6.3."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass

from chronosync.db import engine as db_engine
from chronosync.db import repositories as repos
from chronosync.exceptions import PermanentProviderError, ProviderError
from chronosync.logging import get_logger
from chronosync.providers.base import BaseDataFeed
from chronosync.sync.planner import FetchTask

_log = get_logger(__name__)


@dataclass(slots=True)
class IterationSummary:
    scanned: int = 0
    fetched: int = 0
    upserted: int = 0
    errored: int = 0
    duration_s: float = 0.0


async def run(
    tasks: Sequence[FetchTask],
    *,
    feed: BaseDataFeed,
    concurrency: int,
) -> IterationSummary:
    sem = asyncio.Semaphore(concurrency)
    summary = IterationSummary(scanned=len(tasks))
    started = time.perf_counter()

    async def handle(task: FetchTask) -> None:
        async with sem:
            await _handle_one(task, feed, summary)

    if tasks:
        await asyncio.gather(*(handle(t) for t in tasks))
    summary.duration_s = round(time.perf_counter() - started, 3)
    return summary


async def _handle_one(task: FetchTask, feed: BaseDataFeed, summary: IterationSummary) -> None:
    async with db_engine.session_scope() as s:
        await repos.mark_sync_started(s, instrument_id=task.instrument_id, feed_name=task.feed_name)

    try:
        rows = [
            {
                "ts": b.ts,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "vwap": b.vwap,
                "adj_factor": b.adj_factor,
                "open_interest": b.open_interest,
            }
            async for b in feed.fetch_daily_bars(task.ticker, task.exchange, task.from_date, task.to_date)
        ]
    except PermanentProviderError as e:
        _log.warning(
            "fetch_permanent_error",
            ticker=task.ticker,
            exchange=task.exchange,
            err=str(e),
        )
        summary.errored += 1
        async with db_engine.session_scope() as s:
            await repos.mark_sync_error(
                s,
                instrument_id=task.instrument_id,
                feed_name=task.feed_name,
                error=f"permanent: {e}",
            )
        return
    except ProviderError as e:
        _log.warning(
            "fetch_retriable_exhausted",
            ticker=task.ticker,
            exchange=task.exchange,
            err=str(e),
        )
        summary.errored += 1
        async with db_engine.session_scope() as s:
            await repos.mark_sync_error(
                s,
                instrument_id=task.instrument_id,
                feed_name=task.feed_name,
                error=str(e),
            )
        return

    summary.fetched += len(rows)
    if not rows:
        return

    async with db_engine.session_scope() as s:
        upserted = await repos.upsert_daily_bars(
            s,
            instrument_id=task.instrument_id,
            feed_name=task.feed_name,
            rows=rows,
        )
        summary.upserted += upserted
        await repos.mark_sync_success(
            s,
            instrument_id=task.instrument_id,
            feed_name=task.feed_name,
            last_ts=rows[-1]["ts"],
        )
