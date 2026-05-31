"""Planner delta-math and backfill chunking. Repos faked to avoid DB."""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

from chronosync.sync import planner


class _FakeInstrument:
    def __init__(self, ticker: str, exchange: str = "NSE") -> None:
        self.id = uuid4()
        self.ticker = ticker
        self.exchange = exchange


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_tasks_skips_when_caught_up(monkeypatch: pytest.MonkeyPatch) -> None:
    today = date(2024, 1, 10)
    inst = _FakeInstrument("RELIANCE")

    async def fake_list(_session, exchange):  # type: ignore[no-untyped-def]
        return [inst]

    async def fake_last(_session, *, feed_name, instrument_ids):  # type: ignore[no-untyped-def]
        return {inst.id: today}  # already synced today

    monkeypatch.setattr(planner.repos, "list_active_instruments", fake_list)
    monkeypatch.setattr(planner.repos, "last_synced_map", fake_last)

    tasks = await planner.build_tasks(None, exchange="NSE", feed_name="yfinance", today=today)  # type: ignore[arg-type]
    assert tasks == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_build_tasks_uses_default_lookback_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    today = date(2024, 1, 10)
    inst = _FakeInstrument("RELIANCE")

    async def fake_list(_session, exchange):  # type: ignore[no-untyped-def]
        return [inst]

    async def fake_last(_session, *, feed_name, instrument_ids):  # type: ignore[no-untyped-def]
        return {}

    monkeypatch.setattr(planner.repos, "list_active_instruments", fake_list)
    monkeypatch.setattr(planner.repos, "last_synced_map", fake_last)

    tasks = await planner.build_tasks(
        None,  # type: ignore[arg-type]
        exchange="NSE",
        feed_name="yfinance",
        today=today,
        default_lookback_days=30,
    )
    assert len(tasks) == 1
    t = tasks[0]
    assert t.to_date == today
    assert t.from_date == today - timedelta(days=30)


@pytest.mark.unit
def test_split_backfill_chunks() -> None:
    iid = uuid4()
    tasks = planner.split_backfill(
        iid, "T", "NSE", "yfinance",
        date(2024, 1, 1), date(2024, 1, 10),
        batch_days=3,
    )
    assert [(t.from_date, t.to_date) for t in tasks] == [
        (date(2024, 1, 1), date(2024, 1, 3)),
        (date(2024, 1, 4), date(2024, 1, 6)),
        (date(2024, 1, 7), date(2024, 1, 9)),
        (date(2024, 1, 10), date(2024, 1, 10)),
    ]


@pytest.mark.unit
def test_split_backfill_empty_when_inverted() -> None:
    iid = uuid4()
    assert planner.split_backfill(iid, "T", "NSE", "f", date(2024, 2, 1), date(2024, 1, 1), batch_days=5) == []
