"""End-to-end meta refresh against a real DB; provider monkeypatched."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from chronosync.db import repositories as repos
from chronosync.db.types import AssetClass
from chronosync.providers.base import InstrumentMeta
from chronosync.sync import meta_refresh


class _StubFeed:
    name = "stub"
    supports_intraday = False
    supports_adjusted = True

    def __init__(self, by_ticker: dict[str, Decimal | None]) -> None:
        self._by_ticker = by_ticker

    async def fetch_daily_bars(self, *_a, **_k):
        if False:
            yield  # pragma: no cover

    async def fetch_instrument_meta(self, ticker: str, exchange: str):  # type: ignore[no-untyped-def]
        mc = self._by_ticker.get(ticker)
        if mc is None and ticker not in self._by_ticker:
            return None
        return InstrumentMeta(
            ticker=ticker,
            exchange=exchange,
            asset_class=AssetClass.EQUITY,
            country_code="IN",
            currency="INR",
            isin=None,
            market_cap=mc,
            is_active=True,
        )

    async def close(self) -> None:
        return None


@pytest.mark.functional
@pytest.mark.requires_docker
@pytest.mark.asyncio
async def test_refresh_persists_mcap_and_preserves_existing_on_empty(db_session) -> None:
    rows = [
        {
            "ticker": t,
            "exchange": "NSE",
            "asset_class": "EQUITY",
            "country_code": "IN",
            "currency": "INR",
            "isin": None,
            "is_active": True,
        }
        for t in ("MCAPA", "MCAPB", "MCAPMISS")
    ]
    await repos.upsert_instruments(db_session, rows)
    await db_session.commit()

    # Pre-seed MCAPMISS with an existing market_cap so we can prove a None
    # provider response doesn't clobber it.
    miss = await repos.get_instrument(db_session, "MCAPMISS")
    assert miss is not None
    await repos.update_instrument_market_cap(
        db_session, instrument_id=miss.id, market_cap=Decimal("9999999"), as_of=date(2025, 1, 1)
    )
    await db_session.commit()

    # Scope to just the three instruments this test inserted (the DB may have
    # leftover state from other functional tests sharing this container).
    insts = [
        await repos.get_instrument(db_session, t) for t in ("MCAPA", "MCAPB", "MCAPMISS")
    ]
    insts = [i for i in insts if i is not None]
    assert len(insts) == 3

    feed = _StubFeed(
        {
            "MCAPA": Decimal("1500000"),
            "MCAPB": Decimal("2500000"),
            "MCAPMISS": None,  # provider has no value
        }
    )
    summary = await meta_refresh.run(insts, feed=feed, concurrency=2, today=date(2026, 5, 31))
    assert summary.scanned == 3
    assert summary.updated == 2
    assert summary.empty == 1

    # meta_refresh.run opens its own sessions; invalidate this session's
    # identity map so re-reads actually hit the DB.
    db_session.expire_all()
    a = await repos.get_instrument(db_session, "MCAPA")
    b = await repos.get_instrument(db_session, "MCAPB")
    miss_after = await repos.get_instrument(db_session, "MCAPMISS")
    assert a is not None and a.market_cap == Decimal("1500000.00") and a.market_cap_as_of == date(2026, 5, 31)
    assert b is not None and b.market_cap == Decimal("2500000.00")
    # Pre-existing value preserved despite empty provider response.
    assert miss_after is not None and miss_after.market_cap == Decimal("9999999.00")
    assert miss_after.market_cap_as_of == date(2025, 1, 1)
