"""Daemon._refresh_meta_if_stale: fires only when market_cap drifts past the window."""

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

    async def fake_run_meta() -> None:
        fired.append(True)

    @asynccontextmanager
    async def fake_scope() -> AsyncIterator[object]:
        yield object()

    monkeypatch.setattr(daemon_mod.db_engine, "session_scope", fake_scope)
    monkeypatch.setattr(d, "run_meta_iteration", fake_run_meta)
    return d, fired, monkeypatch


def _set_watermark(monkeypatch, wm):  # type: ignore[no-untyped-def]
    async def fake_wm(_s, *, exchange=None):  # type: ignore[no-untyped-def]
        return wm

    monkeypatch.setattr(daemon_mod.repos, "market_cap_watermark", fake_wm)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_skip_on_cold_db(guard_daemon) -> None:
    d, fired, mp = guard_daemon
    _set_watermark(mp, None)
    await d._refresh_meta_if_stale(now=datetime(2026, 8, 20, 10, 0))
    assert fired == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_skip_when_fresh(guard_daemon) -> None:
    d, fired, mp = guard_daemon
    _set_watermark(mp, date(2026, 8, 18))  # 2 days old, < 8-day window
    await d._refresh_meta_if_stale(now=datetime(2026, 8, 20, 10, 0))
    assert fired == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_refresh_when_stale(guard_daemon) -> None:
    d, fired, mp = guard_daemon
    _set_watermark(mp, date(2026, 8, 1))  # 19 days old, past the window
    await d._refresh_meta_if_stale(now=datetime(2026, 8, 20, 10, 0))
    assert fired == [True]
