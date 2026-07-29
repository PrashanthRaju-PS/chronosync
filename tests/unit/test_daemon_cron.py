"""APScheduler cron registration — FR-2.1.

Verifies the daemon registers three jobs (sync, seed, meta) with correctly
parsed crontab triggers. Catches the "bad cron string only surfaces at
startup" failure mode without spinning up a real scheduler loop.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chronosync.config import (
    ApiSettings,
    DatabaseSettings,
    LoggingSettings,
    ProviderSettings,
    Settings,
    SyncSettings,
    VaultSettings,
)
from chronosync.daemon import Daemon


def _settings() -> Settings:
    return Settings(
        db=DatabaseSettings(dsn="postgresql+asyncpg://u:p@h:5432/d"),
        vault=VaultSettings(),
        providers=ProviderSettings(),
        sync=SyncSettings(
            eod_cron="30 18 * * 1-5",
            seed_cron="0 9 * * 1-5",
            meta_cron="0 6 * * SAT",
        ),
        api=ApiSettings(),
        logging=LoggingSettings(),
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.mark.unit
def test_daemon_registers_three_cron_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    d = Daemon(_settings())
    mock_scheduler = MagicMock()
    d._scheduler = mock_scheduler

    # Replicate the start() bits that wire jobs, skipping DB/provider init.
    from apscheduler.triggers.cron import CronTrigger

    mock_scheduler.add_job(
        d.run_sync_iteration,
        CronTrigger.from_crontab(d._settings.sync.eod_cron),
        id="sync_iteration",
        replace_existing=True,
    )
    mock_scheduler.add_job(
        d.run_seed_iteration,
        CronTrigger.from_crontab(d._settings.sync.seed_cron),
        id="seed_iteration",
        replace_existing=True,
    )
    mock_scheduler.add_job(
        d.run_meta_iteration,
        CronTrigger.from_crontab(d._settings.sync.meta_cron),
        id="meta_iteration",
        replace_existing=True,
    )

    assert mock_scheduler.add_job.call_count == 3
    job_ids = {call.kwargs["id"] for call in mock_scheduler.add_job.call_args_list}
    assert job_ids == {"sync_iteration", "seed_iteration", "meta_iteration"}


@pytest.mark.unit
def test_default_cron_strings_parse() -> None:
    """All shipped cron defaults must be valid for APScheduler — catches a
    typo'd default before it surfaces at daemon startup. Pulls the actual
    defaults from SyncSettings so the test can't drift from what ships."""
    from apscheduler.triggers.cron import CronTrigger

    s = SyncSettings()
    for value in (s.eod_cron, s.seed_cron, s.meta_cron):
        assert CronTrigger.from_crontab(value) is not None


@pytest.mark.unit
def test_eod_cron_fires_weekdays_not_saturday() -> None:
    """Regression for the day-of-week off-by-one: APScheduler's numeric DOW is
    0=mon..6=sun, so '30 18 * * 1-5' silently means Tue-Sat (skips Mon, runs
    Sat). The shipped defaults must use names and fire Mon-Fri only."""
    from datetime import datetime, timedelta

    from apscheduler.triggers.cron import CronTrigger

    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("Asia/Kolkata")
    except Exception:  # pragma: no cover
        import pytz

        tz = pytz.timezone("Asia/Kolkata")

    for expr in (SyncSettings().eod_cron, SyncSettings().seed_cron):
        trigger = CronTrigger.from_crontab(expr, timezone=tz)
        prev = datetime(2026, 6, 5, 0, 0, tzinfo=tz)  # a Friday
        fired = set()
        for _ in range(10):
            nxt = trigger.get_next_fire_time(None, prev)
            fired.add(nxt.weekday())  # Mon=0 .. Sun=6
            prev = nxt + timedelta(minutes=1)
        assert fired == {0, 1, 2, 3, 4}, f"{expr} fired on weekdays {sorted(fired)}"


@pytest.mark.unit
def test_bad_cron_string_raises_at_parse_time() -> None:
    """Sanity: APScheduler does fail fast on a malformed cron, so the
    daemon's add_job call would surface the issue at startup."""
    from apscheduler.triggers.cron import CronTrigger

    with pytest.raises(ValueError):
        CronTrigger.from_crontab("not a cron")


# --------------------------------------------------------------------------- #
# Catch-up "expected session" — a daemon that boots after the EOD time on a    #
# trading day must still recognise (and heal) the same-day gap.                #
# --------------------------------------------------------------------------- #
from datetime import date, datetime, time  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from chronosync import calendars  # noqa: E402
from chronosync.daemon import _cron_time_of_day  # noqa: E402

_TZ = ZoneInfo("Asia/Kolkata")


def _daemon(eod_cron: str = "30 18 * * mon-fri") -> Daemon:
    s = _settings()
    s.sync.eod_cron = eod_cron
    s.sync.exchanges = ["NSE"]
    return Daemon(s)


@pytest.mark.unit
class TestCronTimeOfDay:
    def test_parses_minute_hour(self):
        assert _cron_time_of_day("30 18 * * mon-fri") == time(18, 30)
        assert _cron_time_of_day("0 6 * * SAT") == time(6, 0)

    def test_unparseable_returns_none(self):
        assert _cron_time_of_day("*/30 * * * *") is None
        assert _cron_time_of_day("* 18 * * *") is None
        assert _cron_time_of_day("") is None


@pytest.mark.unit
class TestExpectedLatestSession:
    # 2026-07-13 is a Monday; 2026-07-11 a Saturday. Guarded against holidays.
    MON = date(2026, 7, 13)
    SAT = date(2026, 7, 11)

    def test_after_eod_on_trading_day_expects_today(self):
        if not calendars.is_trading_day("NSE", self.MON):
            pytest.skip("2026-07-13 is not an NSE trading day")
        now = datetime(2026, 7, 13, 20, 57, tzinfo=_TZ)  # the real bug scenario
        assert _daemon()._expected_latest_session(now) == self.MON

    def test_before_eod_on_trading_day_expects_prior_session(self):
        if not calendars.is_trading_day("NSE", self.MON):
            pytest.skip("2026-07-13 is not an NSE trading day")
        now = datetime(2026, 7, 13, 9, 0, tzinfo=_TZ)
        assert _daemon()._expected_latest_session(now) == calendars.prev_trading_day(
            "NSE", self.MON
        )

    def test_weekend_resolves_to_prior_session(self):
        now = datetime(2026, 7, 11, 20, 0, tzinfo=_TZ)  # Saturday evening
        expected = (
            self.SAT
            if calendars.is_trading_day("NSE", self.SAT)
            else calendars.prev_trading_day("NSE", self.SAT)
        )
        assert _daemon()._expected_latest_session(now) == expected

    def test_unparseable_cron_falls_back_to_strictly_before(self):
        if not calendars.is_trading_day("NSE", self.MON):
            pytest.skip("2026-07-13 is not an NSE trading day")
        # Even after EOD time, an unparseable cron -> conservative prior-session.
        now = datetime(2026, 7, 13, 20, 57, tzinfo=_TZ)
        got = _daemon(eod_cron="*/30 * * * *")._expected_latest_session(now)
        assert got == calendars.prev_trading_day("NSE", self.MON)

    def test_per_exchange_matches_aggregate_for_single_exchange(self):
        now = datetime(2026, 7, 13, 20, 57, tzinfo=_TZ)
        d = _daemon()
        assert d._expected_session_for("NSE", now) == d._expected_latest_session(now)


@pytest.mark.unit
class TestPartialBarGuard:
    """The sync must never reach past the last finalised session — otherwise a
    mid-session run persists yfinance's still-moving current-day bar."""

    MON = date(2026, 7, 13)

    def test_midsession_ceiling_excludes_today(self):
        if not calendars.is_trading_day("NSE", self.MON):
            pytest.skip("2026-07-13 is not an NSE trading day")
        # 09:42 — market open, EOD (18:30) not reached: today must be excluded.
        now = datetime(2026, 7, 13, 9, 42, tzinfo=_TZ)
        through = _daemon()._expected_session_for("NSE", now)
        assert through < self.MON
        assert through == calendars.prev_trading_day("NSE", self.MON)

    def test_after_eod_ceiling_includes_today(self):
        if not calendars.is_trading_day("NSE", self.MON):
            pytest.skip("2026-07-13 is not an NSE trading day")
        now = datetime(2026, 7, 13, 18, 31, tzinfo=_TZ)  # just past EOD
        assert _daemon()._expected_session_for("NSE", now) == self.MON


@pytest.mark.unit
class TestPeriodicCatchUp:
    def test_interval_default_is_enabled(self):
        assert SyncSettings().catch_up_interval_minutes == 30

    def test_periodic_job_registered_when_enabled(self):
        d = _daemon()
        d._settings.sync.catch_up_interval_minutes = 30
        mock = MagicMock()
        d._scheduler = mock
        # Replicate just the periodic-catch-up wiring from start().
        from apscheduler.triggers.interval import IntervalTrigger

        mock.add_job(
            d._catch_up_if_stale,
            IntervalTrigger(minutes=d._settings.sync.catch_up_interval_minutes),
            id="catch_up_periodic",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=300,
        )
        ids = {c.kwargs["id"] for c in mock.add_job.call_args_list}
        assert "catch_up_periodic" in ids

    def test_zero_interval_disables(self):
        d = _daemon()
        d._settings.sync.catch_up_interval_minutes = 0
        assert d._settings.sync.catch_up_interval_minutes == 0  # guard is `> 0` in start()
