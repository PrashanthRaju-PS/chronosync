"""Bounded-concurrency worker that refreshes instruments.market_cap snapshots.

Calls feed.fetch_instrument_meta(...) per active instrument; persists only the
market_cap field (with as_of) via update_instrument_market_cap. Missing/None
values are intentionally skipped so a transient provider miss never clobbers
an existing good value.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from chronosync.db import engine as db_engine
from chronosync.db import repositories as repos
from chronosync.db.models import Instrument
from chronosync.exceptions import ProviderError
from chronosync.logging import get_logger
from chronosync.providers.base import BaseDataFeed

_log = get_logger(__name__)


@dataclass(slots=True)
class MetaRefreshSummary:
    scanned: int = 0
    updated: int = 0
    empty: int = 0
    errored: int = 0
    duration_s: float = 0.0


async def run(
    instruments: Sequence[Instrument],
    *,
    feed: BaseDataFeed,
    concurrency: int,
    today: date | None = None,
) -> MetaRefreshSummary:
    sem = asyncio.Semaphore(concurrency)
    summary = MetaRefreshSummary(scanned=len(instruments))
    started = time.perf_counter()
    as_of = today or date.today()

    async def handle(inst: Instrument) -> None:
        async with sem:
            try:
                meta = await feed.fetch_instrument_meta(inst.ticker, inst.exchange)
            except ProviderError as e:
                summary.errored += 1
                _log.warning(
                    "meta_fetch_error",
                    ticker=inst.ticker,
                    exchange=inst.exchange,
                    err=str(e),
                )
                return
            if meta is None or meta.market_cap is None:
                summary.empty += 1
                return
            async with db_engine.session_scope() as s:
                n = await repos.update_instrument_market_cap(
                    s,
                    instrument_id=inst.id,
                    market_cap=meta.market_cap,
                    as_of=as_of,
                )
                summary.updated += n

    if instruments:
        await asyncio.gather(*(handle(i) for i in instruments))
    summary.duration_s = round(time.perf_counter() - started, 3)
    return summary
