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
    retry_max_attempts: int = 5
    retry_base_delay_s: float = 1.0
    retry_max_delay_s: float = 30.0


class SyncSettings(BaseModel):
    exchanges: list[str] = Field(default_factory=lambda: ["NSE", "BSE"])
    eod_cron: str = "30 18 * * 1-5"
    seed_cron: str = "0 9 * * 1-5"
    meta_cron: str = "0 6 * * SAT"  # weekly market_cap refresh
    timezone: str = "Asia/Kolkata"
    backfill_batch_days: int = 30


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
