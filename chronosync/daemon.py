"""Long-running asyncio supervisor. See docs/HLD.md §3.1, LLD §6.1."""

from __future__ import annotations

import asyncio
import signal
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from chronosync import calendars
from chronosync.config import Settings, get_settings
from chronosync.db import engine as db_engine
from chronosync.db import repositories as repos
from chronosync.logging import configure_logging, get_logger
from chronosync.providers import registry as provider_registry
from chronosync.seeders import BSEBhavcopySeeder, NSEBhavcopySeeder
from chronosync.sync import fno_refresh, meta_refresh, planner, worker

_log = get_logger(__name__)


def _cron_time_of_day(cron: str) -> time | None:
    """Best-effort ``time`` from the minute+hour of a simple ``"<min> <hour> …"``
    cron expression. Returns None when those fields aren't plain integers (e.g.
    ``*`` or ``*/30``), so callers can fall back to conservative behaviour."""
    parts = cron.split()
    if len(parts) < 2:
        return None
    try:
        return time(hour=int(parts[1]), minute=int(parts[0]))
    except ValueError:
        return None


class Daemon:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._scheduler = AsyncIOScheduler(timezone=self._settings.sync.timezone)
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        configure_logging(self._settings.logging)
        db_engine.init_engine(self._settings.db)
        await db_engine.ping()
        provider_registry.bootstrap_registry(self._settings.providers)

        # coalesce + a grace window so a job that misfires while the scheduler
        # is *alive* (event loop blocked, prior run overran) still runs once
        # rather than being silently dropped. This does NOT cover a process-down
        # gap — that's what _catch_up_if_stale below handles.
        self._scheduler.add_job(
            self.run_sync_iteration,
            CronTrigger.from_crontab(self._settings.sync.eod_cron),
            id="sync_iteration",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=3600,
        )
        self._scheduler.add_job(
            self.run_seed_iteration,
            CronTrigger.from_crontab(self._settings.sync.seed_cron),
            id="seed_iteration",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=3600,
        )
        self._scheduler.add_job(
            self.run_meta_iteration,
            CronTrigger.from_crontab(self._settings.sync.meta_cron),
            id="meta_iteration",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=6 * 3600,
        )
        self._scheduler.add_job(
            self.run_fno_iteration,
            CronTrigger.from_crontab(self._settings.sync.fno_cron),
            id="fno_iteration",
            replace_existing=True,
            coalesce=True,
            misfire_grace_time=6 * 3600,
        )
        # Self-heal a sync missed while the daemon was down: a no-trigger job
        # runs once, immediately, after the scheduler starts.
        if self._settings.sync.catch_up_on_start:
            self._scheduler.add_job(
                self._catch_up_if_stale,
                id="catch_up_on_start",
                replace_existing=True,
            )
            # Same idea for the monthly F&O refresh: a cron fire on the 1st is
            # never replayed if the machine slept through it, so reconcile against
            # fno_as_of at start (cheap, idempotent, runs at most once a month).
            self._scheduler.add_job(
                self._refresh_fno_if_stale,
                id="fno_catch_up_on_start",
                replace_existing=True,
            )
            # ...and the weekly market_cap refresh: heal it if market_cap_as_of has
            # drifted past meta_stale_days (missed Saturday, or a fresh container).
            self._scheduler.add_job(
                self._refresh_meta_if_stale,
                id="meta_catch_up_on_start",
                replace_existing=True,
            )
        # ...and keep re-checking. A cron fire that elapses while the process or
        # the host VM is suspended is never replayed, so start-up alone isn't
        # enough on a machine that sleeps through the EOD window: without this the
        # gap survives until the next restart. max_instances=1 so a long sync
        # can't stack up re-entrant catch-ups behind it.
        if self._settings.sync.catch_up_interval_minutes > 0:
            self._scheduler.add_job(
                self._catch_up_if_stale,
                IntervalTrigger(minutes=self._settings.sync.catch_up_interval_minutes),
                id="catch_up_periodic",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=300,
            )
            # ...and re-check the monthly F&O refresh on the same cadence, so a
            # machine that was asleep on the 1st heals shortly after it wakes.
            self._scheduler.add_job(
                self._refresh_fno_if_stale,
                IntervalTrigger(minutes=self._settings.sync.catch_up_interval_minutes),
                id="fno_catch_up_periodic",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=300,
            )
            self._scheduler.add_job(
                self._refresh_meta_if_stale,
                IntervalTrigger(minutes=self._settings.sync.catch_up_interval_minutes),
                id="meta_catch_up_periodic",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=300,
            )
        self._scheduler.start()
        _log.info(
            "daemon_started",
            eod_cron=self._settings.sync.eod_cron,
            seed_cron=self._settings.sync.seed_cron,
            meta_cron=self._settings.sync.meta_cron,
            fno_cron=self._settings.sync.fno_cron,
            tz=self._settings.sync.timezone,
            exchanges=self._settings.sync.exchanges,
        )

        self._install_signal_handlers()
        await self._stop_event.wait()
        await self.shutdown()

    async def shutdown(self) -> None:
        _log.info("daemon_shutdown_begin")
        self._scheduler.shutdown(wait=False)
        await provider_registry.get_registry().close_all()
        await db_engine.dispose()
        _log.info("daemon_shutdown_done")

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._stop_event.set)
            except NotImplementedError:
                # Windows: rely on KeyboardInterrupt from asyncio.run wrapper.
                signal.signal(sig, lambda *_: self._stop_event.set())

    # ----- jobs -----

    def _expected_session_for(self, exchange: str, now: datetime | None = None) -> date:
        """The most recent *finalised* trading session for one exchange.

        Baseline is the day strictly before today — today's own bar is the EOD
        cron's job and, mid-session, does not exist yet in final form. BUT once
        today's EOD sync time has passed on a trading day, today's session is
        complete and genuinely expected: a daemon that boots *after* the EOD time
        (e.g. down at 18:30, started at 21:00) must still recognise the same-day
        gap and self-heal rather than waiting for tomorrow's cron.

        Doubles as the fetch ceiling for a sync (see `run_sync_iteration`): never
        reaching past this date is what stops a mid-session run from persisting a
        half-formed bar for the current day.

        The EOD time comes from the configured ``eod_cron`` (evaluated in the
        configured timezone); if it can't be parsed we fall back to the
        strictly-before-today baseline. `now` is injectable for tests.
        """
        tz = ZoneInfo(self._settings.sync.timezone)
        now = (now or datetime.now(tz)).astimezone(tz)
        eod = _cron_time_of_day(self._settings.sync.eod_cron)

        # Reference day: today once its EOD time has passed, else yesterday. The
        # trading calendar then maps that onto the latest actual session <= ref
        # (so a Saturday/holiday reference resolves back to the prior session).
        if eod is not None and now.time() >= eod:
            ref = now.date()
        else:
            ref = now.date() - timedelta(days=1)
        if calendars.is_trading_day(exchange, ref):
            return ref
        return calendars.prev_trading_day(exchange, ref)

    def _expected_latest_session(self, now: datetime | None = None) -> date:
        """`_expected_session_for` across every configured exchange (the most
        recent session any of them should have) — the staleness bar for catch-up."""
        return max(self._expected_session_for(ex, now) for ex in self._settings.sync.exchanges)

    async def _catch_up_if_stale(self) -> None:
        """Run one immediate sync if a scheduled run was missed while the daemon
        was down.

        The in-memory scheduler keeps no state across restarts, so a misfire
        can't fire for a process that wasn't running — on restart it only
        schedules the next future run. Instead of relying on the schedule, we
        reconcile against the data watermark: if the newest synced bar is older
        than the most recent expected session (see `_expected_latest_session`),
        fire a sync now and let cron resume. The sync path is incremental and
        idempotent, so this is a cheap no-op when already current.

        Skipped on a cold DB (no prior sync) so we don't trigger a full-lookback
        backfill on first boot — seeding and the normal cron handle first fill.
        """
        async with db_engine.session_scope() as s:
            last = await repos.last_synced_max(s, feed_name=self._settings.providers.default_feed)
        if last is None:
            _log.info("catch_up_skipped", reason="cold_db")
            return

        expected = self._expected_latest_session()
        if last >= expected:
            _log.info(
                "catch_up_skipped",
                reason="current",
                last_synced=last.isoformat(),
                expected=expected.isoformat(),
            )
            return

        _log.info("catch_up_triggered", last_synced=last.isoformat(), expected=expected.isoformat())
        await self.run_sync_iteration()

    async def run_sync_iteration(self) -> None:
        feed_name = self._settings.providers.default_feed
        feed = provider_registry.get_registry().get(feed_name)
        for exchange in self._settings.sync.exchanges:
            # Never fetch past the last *finalised* session. yfinance serves the
            # current day as a live, still-moving bar, so a run during market
            # hours (catch-up, or a manual one-off) would otherwise persist a
            # half-formed bar — a wrong close/high/low and a fraction of the
            # day's volume — which then feeds the scanners as if it were real.
            # Today is only included once its EOD time has passed.
            through = self._expected_session_for(exchange)
            async with db_engine.session_scope() as s:
                tasks = await planner.build_tasks(
                    s, exchange=exchange, feed_name=feed_name, today=through
                )
            # Start line: pairs with sync_iteration_done so the log shows a run
            # is in flight (and its size) rather than only that one finished.
            _log.info(
                "sync_iteration_start",
                exchange=exchange,
                feed=feed_name,
                pending=len(tasks),
                through=through.isoformat(),
            )
            summary = await worker.run(
                tasks,
                feed=feed,
                concurrency=self._settings.providers.yfinance_concurrency,
            )
            _log.info(
                "sync_iteration_done",
                exchange=exchange,
                feed=feed_name,
                scanned=summary.scanned,
                fetched=summary.fetched,
                upserted=summary.upserted,
                errored=summary.errored,
                duration_s=summary.duration_s,
            )
            # End-of-run rollup naming the failed tickers, so you don't have to
            # scrape individual WARN lines between two iteration markers. Capped
            # to keep the line bounded on a bad day.
            if summary.errored:
                _log.warning(
                    "sync_iteration_errors",
                    exchange=exchange,
                    feed=feed_name,
                    errored=summary.errored,
                    tickers=summary.errored_tickers[:50],
                    truncated=max(0, summary.errored - 50),
                )

    async def run_meta_iteration(self) -> None:
        feed_name = self._settings.providers.default_feed
        feed = provider_registry.get_registry().get(feed_name)
        from chronosync.db import repositories as repos

        for exchange in self._settings.sync.exchanges:
            async with db_engine.session_scope() as s:
                instruments = await repos.list_active_instruments(s, exchange=exchange)
            summary = await meta_refresh.run(
                instruments,
                feed=feed,
                concurrency=self._settings.providers.meta_concurrency,
            )
            _log.info(
                "meta_iteration_done",
                exchange=exchange,
                feed=feed_name,
                scanned=summary.scanned,
                updated=summary.updated,
                empty=summary.empty,
                errored=summary.errored,
                duration_s=summary.duration_s,
            )

    async def _refresh_meta_if_stale(self, now: datetime | None = None) -> None:
        """Run the weekly market_cap refresh if `market_cap_as_of` has drifted
        past meta_stale_days (missed Saturday cron, or a freshly-built container).

        Gated by staleness so a full-universe yfinance sweep runs at most about
        once a week, never on every boot. Skipped on a cold DB (nothing seeded
        yet) so first fill is left to the normal cron.
        """
        tz = ZoneInfo(self._settings.sync.timezone)
        ref = (now or datetime.now(tz)).astimezone(tz).date()
        async with db_engine.session_scope() as s:
            wm = await repos.market_cap_watermark(s)
        if wm is None:
            _log.info("meta_refresh_skipped", reason="cold_db")
            return
        age = (ref - wm).days
        if age < self._settings.sync.meta_stale_days:
            _log.info("meta_refresh_skipped", reason="current", market_cap_as_of=wm.isoformat())
            return
        _log.info("meta_refresh_triggered", market_cap_as_of=wm.isoformat(), age_days=age)
        await self.run_meta_iteration()

    async def run_fno_iteration(self) -> None:
        """Refresh instruments.is_fno from the exchange's F&O underlying list.
        NSE-only for now (the only exchange with a wired derivatives source)."""
        if "NSE" not in self._settings.sync.exchanges:
            return
        summary = await fno_refresh.run(exchange="NSE")
        _log.info(
            "fno_iteration_done",
            exchange=summary.exchange,
            fetched=summary.fetched,
            marked=summary.marked,
            cleared=summary.cleared,
            duration_s=summary.duration_s,
        )

    async def _refresh_fno_if_stale(self, now: datetime | None = None) -> None:
        """Run the monthly F&O refresh if it hasn't run yet this calendar month.

        Covers a scheduled fire missed while the process — or the whole VM — was
        suspended, mirroring `_catch_up_if_stale` for sync. A single MAX query
        when already current, and at most one refresh per month.
        """
        if "NSE" not in self._settings.sync.exchanges:
            return
        tz = ZoneInfo(self._settings.sync.timezone)
        ref = (now or datetime.now(tz)).astimezone(tz).date()
        async with db_engine.session_scope() as s:
            wm = await repos.fno_watermark(s, exchange="NSE")
        # wm in the current (or a later) month means this month's refresh is done.
        if wm is not None and (wm.year, wm.month) >= (ref.year, ref.month):
            _log.info("fno_refresh_skipped", reason="current", fno_as_of=wm.isoformat())
            return
        _log.info(
            "fno_refresh_triggered",
            fno_as_of=wm.isoformat() if wm else None,
            ref=ref.isoformat(),
        )
        await self.run_fno_iteration()

    async def run_seed_iteration(self) -> None:
        today = date.today()
        for exchange in self._settings.sync.exchanges:
            seeder = (
                NSEBhavcopySeeder()
                if exchange == "NSE"
                else BSEBhavcopySeeder()
                if exchange == "BSE"
                else None
            )
            if seeder is None:
                continue
            try:
                async with db_engine.session_scope() as s:
                    touched = await seeder.run(s)
                _log.info(
                    "seed_iteration_done", exchange=exchange, touched=touched, day=today.isoformat()
                )
            except Exception as e:  # noqa: BLE001
                _log.warning("seed_iteration_failed", exchange=exchange, err=str(e))


async def run_once() -> None:
    """One iteration then exit — cron-friendly entry."""
    d = Daemon()
    configure_logging(d._settings.logging)
    db_engine.init_engine(d._settings.db)
    await db_engine.ping()
    provider_registry.bootstrap_registry(d._settings.providers)
    try:
        await d.run_sync_iteration()
    finally:
        await provider_registry.get_registry().close_all()
        await db_engine.dispose()
