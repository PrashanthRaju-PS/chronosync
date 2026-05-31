"""Meta-refresh worker behaviour: skip-on-None semantics and tally accuracy.

Repository + DB engine are faked so this stays a true unit test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from chronosync.db.types import AssetClass
from chronosync.exceptions import PermanentProviderError
from chronosync.providers.base import InstrumentMeta
from chronosync.sync import meta_refresh


@dataclass
class _FakeInst:
    id: object
    ticker: str
    exchange: str = "NSE"


class _FakeFeed:
    name = "fake"
    supports_intraday = False
    supports_adjusted = True

    def __init__(self, meta_map: dict[str, InstrumentMeta | None], raise_for: set[str] | None = None):
        self._meta = meta_map
        self._raise = raise_for or set()
        self.calls: list[str] = []

    async def fetch_daily_bars(self, *_a, **_k):  # not used here
        if False:
            yield  # pragma: no cover

    async def fetch_instrument_meta(self, ticker: str, exchange: str):  # type: ignore[no-untyped-def]
        self.calls.append(ticker)
        if ticker in self._raise:
            raise PermanentProviderError(f"boom {ticker}")
        return self._meta.get(ticker)

    async def close(self) -> None:
        return None


@pytest.fixture
def patched_session(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Replace db_engine.session_scope + repos.update_instrument_market_cap with fakes."""
    calls: list[dict] = []

    async def fake_update(_session, *, instrument_id, market_cap, as_of):  # type: ignore[no-untyped-def]
        calls.append({"instrument_id": instrument_id, "market_cap": market_cap, "as_of": as_of})
        return 1

    @asynccontextmanager
    async def fake_scope() -> AsyncIterator[object]:
        yield object()

    monkeypatch.setattr(meta_refresh.db_engine, "session_scope", fake_scope)
    monkeypatch.setattr(meta_refresh.repos, "update_instrument_market_cap", fake_update)
    return calls


def _meta(market_cap: Decimal | None) -> InstrumentMeta:
    return InstrumentMeta(
        ticker="X",
        exchange="NSE",
        asset_class=AssetClass.EQUITY,
        country_code="IN",
        currency="INR",
        isin=None,
        market_cap=market_cap,
        is_active=True,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_updates_only_for_non_null_market_cap(patched_session) -> None:
    insts = [
        _FakeInst(id=uuid4(), ticker="GOOD1"),
        _FakeInst(id=uuid4(), ticker="GOOD2"),
        _FakeInst(id=uuid4(), ticker="EMPTY"),
        _FakeInst(id=uuid4(), ticker="NULLMC"),
    ]
    feed = _FakeFeed(
        {
            "GOOD1": _meta(Decimal("1000000")),
            "GOOD2": _meta(Decimal("2000000")),
            "EMPTY": None,
            "NULLMC": _meta(None),
        }
    )

    summary = await meta_refresh.run(insts, feed=feed, concurrency=2, today=date(2026, 5, 31))
    assert summary.scanned == 4
    assert summary.updated == 2
    assert summary.empty == 2
    assert summary.errored == 0
    assert len(patched_session) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_provider_errors_counted_not_raised(patched_session) -> None:
    insts = [
        _FakeInst(id=uuid4(), ticker="GOOD"),
        _FakeInst(id=uuid4(), ticker="BOOM"),
    ]
    feed = _FakeFeed({"GOOD": _meta(Decimal("500000"))}, raise_for={"BOOM"})

    summary = await meta_refresh.run(insts, feed=feed, concurrency=2)
    assert summary.updated == 1
    assert summary.errored == 1
    assert summary.empty == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_input_returns_empty_summary(patched_session) -> None:
    summary = await meta_refresh.run([], feed=_FakeFeed({}), concurrency=4)
    assert summary == type(summary)(scanned=0, updated=0, empty=0, errored=0, duration_s=summary.duration_s)
