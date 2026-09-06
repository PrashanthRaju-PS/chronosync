"""Settings — pydantic-settings loaded from env + .env. See docs/LLD.md §3."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

# Push .env into os.environ so consumers that read raw env vars (e.g. the
# local vault backend looking up CHRONOSYNC_VAULT_KEY) see the same values
# pydantic-settings does. Idempotent and a no-op if .env is absent.
load_dotenv()


class DatabaseSettings(BaseModel):
    dsn: PostgresDsn
    pool_min_size: int = 2
    pool_max_size: int = 20


class VaultSettings(BaseModel):
    backend: Literal["local", "keyring", "hashicorp"] = "local"
    path: Path = Field(default_factory=lambda: Path.home() / ".chronosync" / "vault.enc")
    master_key_env: str = "CHRONOSYNC_VAULT_KEY"


class ProviderSettings(BaseModel):
    default_feed: str = "yfinance"
    yfinance_concurrency: int = 8
    # market_cap refresh hits Yahoo's quote endpoints, which rate-limit harder than
    # bar downloads — keep this low so a whole-universe sweep doesn't get throttled
    # to empty. The provider also backoff-retries on 429.
    meta_concurrency: int = 3
    retry_max_attempts: int = 5
    retry_base_delay_s: float = 1.0
    retry_max_delay_s: float = 30.0


class SyncSettings(BaseModel):
    exchanges: list[str] = Field(default_factory=lambda: ["NSE", "BSE"])
    # Day-of-week MUST be names, not 1-5: APScheduler's numeric DOW is 0=mon..6=sun,
    # so CronTrigger.from_crontab("... 1-5") reads as Tue-Sat (skips Mon, runs Sat).
    # Names parse unambiguously. See tests/unit/test_daemon_cron.py.
    eod_cron: str = "30 18 * * mon-fri"
    seed_cron: str = "0 9 * * mon-fri"
    meta_cron: str = "0 6 * * SAT"  # weekly market_cap refresh
    # Heal a missed weekly meta refresh: if the newest market_cap_as_of is older
    # than this many days, the catch-up guard fires one refresh (startup + interval).
    meta_stale_days: int = 8
    fno_cron: str = "0 7 1 * *"  # monthly F&O membership refresh (07:00 on the 1st)
    timezone: str = "Asia/Kolkata"
    backfill_batch_days: int = 30
    # On daemon start, run one immediate sync if data is behind the most recent
    # completed trading session (i.e. a scheduled run was missed while the
    # process was down). See Daemon._catch_up_if_stale.
    catch_up_on_start: bool = True
    # Also re-check on this interval (minutes; 0 disables). A cron fire that
    # elapses while the process — or the whole VM — is suspended is never
    # replayed by APScheduler, so on a laptop that sleeps through 18:30 the only
    # other healing chance is a restart. This periodic re-check heals shortly
    # after the machine wakes instead of waiting for the next EOD. It's a single
    # cheap query when already current.
    catch_up_interval_minutes: int = 30


class ApiSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8088
    health_max_staleness_h: int = 24


class LoggingSettings(BaseModel):
    level: str = "INFO"
    json_format: bool = False
    file: Path | None = None
    max_bytes: int = 50_000_000
    backups: int = 7


class Settings(BaseSettings):
    db: DatabaseSettings
    vault: VaultSettings = Field(default_factory=VaultSettings)
    providers: ProviderSettings = Field(default_factory=ProviderSettings)
    sync: SyncSettings = Field(default_factory=SyncSettings)
    api: ApiSettings = Field(default_factory=ApiSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    model_config = SettingsConfigDict(
        env_prefix="CHRONOSYNC_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
