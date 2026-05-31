"""Long-running asyncio supervisor. See docs/HLD.md §3.1, LLD §6.1."""

from __future__ import annotations

import asyncio
import signal
from datetime import date

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from chronosync.config import Settings, get_settings
from chronosync.db import engine as db_engine
from chronosync.logging import configure_logging, get_logger
from chronosync.providers import registry as provider_registry
from chronosync.seeders import BSEBhavcopySeeder, NSEBhavcopySeeder
from chronosync.sync import meta_refresh, planner, worker

_log = get_logger(__name__)


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

        self._scheduler.add_job(
            self.run_sync_iteration,
            CronTrigger.from_crontab(self._settings.sync.eod_cron),
            id="sync_iteration",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self.run_seed_iteration,
            CronTrigger.from_crontab(self._settings.sync.seed_cron),
            id="seed_iteration",
            replace_existing=True,
        )
        self._scheduler.add_job(
            self.run_meta_iteration,
            CronTrigger.from_crontab(self._settings.sync.meta_cron),
            id="meta_iteration",
            replace_existing=True,
        )
        self._scheduler.start()
        _log.info(
            "daemon_started",
            eod_cron=self._settings.sync.eod_cron,
            seed_cron=self._settings.sync.seed_cron,
            meta_cron=self._settings.sync.meta_cron,
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

    async def run_sync_iteration(self) -> None:
        feed_name = self._settings.providers.default_feed
        feed = provider_registry.get_registry().get(feed_name)
        for exchange in self._settings.sync.exchanges:
            async with db_engine.session_scope() as s:
                tasks = await planner.build_tasks(s, exchange=exchange, feed_name=feed_name)
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
                concurrency=self._settings.providers.yfinance_concurrency,
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

    async def run_seed_iteration(self) -> None:
        today = date.today()
        for exchange in self._settings.sync.exchanges:
            seeder = NSEBhavcopySeeder() if exchange == "NSE" else BSEBhavcopySeeder() if exchange == "BSE" else None
            if seeder is None:
                continue
            try:
                async with db_engine.session_scope() as s:
                    touched = await seeder.run(s)
                _log.info("seed_iteration_done", exchange=exchange, touched=touched, day=today.isoformat())
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
