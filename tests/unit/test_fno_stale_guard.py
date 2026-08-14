"""Daemon._refresh_fno_if_stale: fires at most once per calendar month."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime

import pytest

from chronosync import daemon as daemon_mod
from chronosync.daemon import Daemon


@pytest.fixture
def guard_daemon(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    d = Daemon()
    fired: list[bool] = []

    async def fake_run_fno() -> None:
        fired.append(True)

    @asynccontextmanager
    async def fake_scope() -> AsyncIterator[object]:
        yield object()

    monkeypatch.setattr(daemon_mod.db_engine, "session_scope", fake_scope)
    monkeypatch.setattr(d, "run_fno_iteration", fake_run_fno)
    return d, fired, monkeypatch


def _set_watermark(monkeypatch, wm):  # type: ignore[no-untyped-def]
    async def fake_wm(_s, *, exchange=None):  # type: ignore[no-untyped-def]
        return wm

    monkeypatch.setattr(daemon_mod.repos, "fno_watermark", fake_wm)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_refresh_when_never_run(guard_daemon) -> None:
    d, fired, mp = guard_daemon
    _set_watermark(mp, None)
    await d._refresh_fno_if_stale(now=datetime(2026, 8, 13, 10, 0))
    assert fired == [True]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_refresh_when_last_run_prior_month(guard_daemon) -> None:
    d, fired, mp = guard_daemon
    _set_watermark(mp, date(2026, 7, 31))  # last month
    await d._refresh_fno_if_stale(now=datetime(2026, 8, 1, 7, 0))
    assert fired == [True]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_skip_when_already_refreshed_this_month(guard_daemon) -> None:
    d, fired, mp = guard_daemon
    _set_watermark(mp, date(2026, 8, 3))  # already done in August
    await d._refresh_fno_if_stale(now=datetime(2026, 8, 20, 10, 0))
    assert fired == []
