# ChronoSync — Low-Level Design (LLD)

**Status:** Draft v0.1 · **Companion to:** [HLD.md](HLD.md), [Requirements.md](../Requirements.md)

This document specifies module boundaries, class/function signatures, database DDL, sequence flows, error taxonomy, configuration surface, and the test strategy in enough detail to implement directly.

---

## 1. Module / Package Layout

```
ChronoSync/
├── pyproject.toml
├── docker-compose.yml
├── .env.example
├── .gitignore
├── alembic.ini
├── alembic/
│   ├── env.py
│   └── versions/
│       └── 0001_initial.py
├── chronosync/
│   ├── __init__.py
│   ├── config.py
│   ├── logging.py
│   ├── exceptions.py
│   ├── calendars.py
│   ├── daemon.py
│   ├── cli.py
│   ├── db/
│   │   ├── __init__.py
│   │   ├── engine.py
│   │   ├── models.py
│   │   ├── types.py
│   │   └── repositories.py
│   ├── secrets/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── local.py
│   │   ├── keyring_backend.py
│   │   └── vault.py
│   ├── providers/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── registry.py
│   │   └── yfinance_feed.py
│   ├── seeders/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── nse_bhavcopy.py
│   │   └── bse_bhavcopy.py
│   ├── sync/
│   │   ├── __init__.py
│   │   ├── planner.py
│   │   ├── worker.py
│   │   └── state.py
│   └── api/
│       ├── __init__.py
│       ├── app.py
│       ├── deps.py
│       ├── schemas.py
│       └── routers/
│           ├── __init__.py
│           ├── health.py
│           ├── instruments.py
│           ├── bars.py
│           └── sync.py
├── chronosync_client/
│   ├── __init__.py
│   ├── client.py
│   └── models.py
├── tests/
│   ├── conftest.py
│   ├── unit/
│   │   ├── test_planner.py
│   │   ├── test_calendars.py
│   │   ├── test_repositories_instruments.py
│   │   ├── test_repositories_bars.py
│   │   ├── test_secrets_local.py
│   │   ├── test_provider_yfinance.py     # vcrpy
│   │   ├── test_seeder_bhavcopy.py        # vcrpy
│   │   └── test_config.py
│   └── functional/
│       ├── test_sync_e2e.py               # testcontainers
│       ├── test_api_contract.py
│       ├── test_cli_admin.py
│       └── test_backfill_idempotent.py
└── docs/
    ├── 00_OriginalPrompt.md
    ├── HLD.md
    └── LLD.md
```

---

## 2. Database Schema (DDL)

```sql
-- Extensions
CREATE EXTENSION IF NOT EXISTS "timescaledb";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";  -- gen_random_uuid()

-- ===== Reference =====
CREATE TYPE asset_class AS ENUM ('EQUITY', 'FUTURE', 'OPTION', 'BOND', 'FX', 'CRYPTO');

CREATE TABLE instruments (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ticker             TEXT NOT NULL,
    exchange           TEXT NOT NULL,
    asset_class        asset_class NOT NULL DEFAULT 'EQUITY',
    country_code       CHAR(2) NOT NULL,
    currency           CHAR(3) NOT NULL,
    isin               TEXT,
    market_cap         NUMERIC(20, 2),
    market_cap_as_of   DATE,
    is_active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (ticker, exchange)
);
CREATE INDEX idx_instruments_active ON instruments (is_active) WHERE is_active;
CREATE INDEX idx_instruments_exchange_active ON instruments (exchange, is_active);
CREATE INDEX idx_instruments_isin ON instruments (isin) WHERE isin IS NOT NULL;

-- ===== Bars =====
CREATE TABLE daily_bars (
    instrument_id  UUID NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    ts             DATE NOT NULL,
    open           NUMERIC(20, 6) NOT NULL,
    high           NUMERIC(20, 6) NOT NULL,
    low            NUMERIC(20, 6) NOT NULL,
    close          NUMERIC(20, 6) NOT NULL,
    volume         BIGINT NOT NULL,
    vwap           NUMERIC(20, 6),
    adj_factor     NUMERIC(20, 10) NOT NULL DEFAULT 1.0,
    open_interest  BIGINT,
    feed_name      TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (instrument_id, ts)
);
SELECT create_hypertable('daily_bars', 'ts', chunk_time_interval => INTERVAL '1 year');
ALTER TABLE daily_bars SET (timescaledb.compress, timescaledb.compress_segmentby = 'instrument_id');
SELECT add_compression_policy('daily_bars', INTERVAL '90 days');

CREATE TABLE intraday_bars (
    instrument_id  UUID NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    ts             TIMESTAMPTZ NOT NULL,
    interval       TEXT NOT NULL,           -- '1m','5m','15m','1h'
    open           NUMERIC(20, 6) NOT NULL,
    high           NUMERIC(20, 6) NOT NULL,
    low            NUMERIC(20, 6) NOT NULL,
    close          NUMERIC(20, 6) NOT NULL,
    volume         BIGINT NOT NULL,
    vwap           NUMERIC(20, 6),
    feed_name      TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (instrument_id, ts, interval)
);
SELECT create_hypertable('intraday_bars', 'ts', chunk_time_interval => INTERVAL '7 days');
ALTER TABLE intraday_bars SET (timescaledb.compress, timescaledb.compress_segmentby = 'instrument_id,interval');
SELECT add_compression_policy('intraday_bars', INTERVAL '7 days');
SELECT add_retention_policy('intraday_bars', INTERVAL '730 days');

-- ===== Sync state =====
CREATE TABLE sync_state (
    instrument_id        UUID NOT NULL REFERENCES instruments(id) ON DELETE CASCADE,
    feed_name            TEXT NOT NULL,
    last_synced_ts       DATE,
    last_run_started_at  TIMESTAMPTZ,
    last_run_finished_at TIMESTAMPTZ,
    last_error           TEXT,
    attempt_count        INT NOT NULL DEFAULT 0,
    PRIMARY KEY (instrument_id, feed_name)
);

-- ===== Reserved for portfolio phase =====
CREATE TABLE users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email       TEXT NOT NULL UNIQUE,
    display_name TEXT,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE accounts (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    vendor        TEXT NOT NULL,
    account_label TEXT NOT NULL,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, vendor, account_label)
);

CREATE TABLE vendor_credentials (
    account_id  UUID NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    key_name    TEXT NOT NULL,            -- 'api_key','api_secret','access_token'
    secret_ref  TEXT NOT NULL,            -- opaque vault path
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    rotated_at  TIMESTAMPTZ,
    PRIMARY KEY (account_id, key_name)
);
```

### 2.1 Upsert pattern (used by `repositories.bars.upsert_daily`)

```sql
INSERT INTO daily_bars (instrument_id, ts, open, high, low, close, volume, vwap, adj_factor, open_interest, feed_name)
VALUES (...)
ON CONFLICT (instrument_id, ts) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close,
    volume = EXCLUDED.volume, vwap = EXCLUDED.vwap, adj_factor = EXCLUDED.adj_factor,
    open_interest = EXCLUDED.open_interest, feed_name = EXCLUDED.feed_name;
```

---

## 3. Configuration Surface (`chronosync.config`)

`pydantic-settings` reads from env + `.env`. Single `Settings` model; sub-models for cohesion.

```python
class DatabaseSettings(BaseModel):
    dsn: PostgresDsn  # CHRONOSYNC_DB__DSN
    pool_min_size: int = 2
    pool_max_size: int = 20

class VaultSettings(BaseModel):
    backend: Literal["local", "keyring", "hashicorp"] = "local"
    path: Path = Path.home() / ".chronosync" / "vault.enc"
    master_key_env: str = "CHRONOSYNC_VAULT_KEY"

class ProviderSettings(BaseModel):
    default_feed: str = "yfinance"
    yfinance_concurrency: int = 8
    retry_max_attempts: int = 5
    retry_base_delay_s: float = 1.0
    retry_max_delay_s: float = 30.0

class SyncSettings(BaseModel):
    exchanges: list[str] = ["NSE", "BSE"]
    eod_cron: str = "30 18 * * 1-5"   # 18:30 IST Mon-Fri; daemon converts to UTC
    timezone: str = "Asia/Kolkata"
    seed_cron: str = "0 9 * * 1-5"
    backfill_batch_days: int = 30

class ApiSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8088

class LoggingSettings(BaseModel):
    level: str = "INFO"
    json: bool = False
    file: Path | None = None
    max_bytes: int = 50_000_000
    backups: int = 7

class Settings(BaseSettings):
    db: DatabaseSettings
    vault: VaultSettings = VaultSettings()
    providers: ProviderSettings = ProviderSettings()
    sync: SyncSettings = SyncSettings()
    api: ApiSettings = ApiSettings()
    logging: LoggingSettings = LoggingSettings()

    model_config = SettingsConfigDict(env_prefix="CHRONOSYNC_", env_nested_delimiter="__", env_file=".env")
```

Env example: `CHRONOSYNC_DB__DSN=postgresql+asyncpg://chrono:chrono@localhost:5432/chronosync`.

---

## 4. Domain Types & Models

### 4.1 SQLAlchemy models (`chronosync.db.models`)
Declarative 2.x style with `Mapped[...]` + `mapped_column`. All UUIDs use `postgresql.UUID(as_uuid=True)`. `Numeric` columns map to `decimal.Decimal`.

### 4.2 Provider DTOs (`chronosync.providers.base`)
```python
@dataclass(frozen=True, slots=True)
class BarRow:
    ts: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    vwap: Decimal | None
    adj_factor: Decimal
    open_interest: int | None = None

@dataclass(frozen=True, slots=True)
class InstrumentMeta:
    ticker: str
    exchange: str
    asset_class: AssetClass
    country_code: str
    currency: str
    isin: str | None
    market_cap: Decimal | None
    is_active: bool
```

### 4.3 Planner DTOs (`chronosync.sync.planner`)
```python
@dataclass(frozen=True, slots=True)
class FetchTask:
    instrument_id: UUID
    ticker: str
    exchange: str
    feed_name: str
    from_date: date
    to_date: date
```

---

## 5. Key Interfaces

### 5.1 `BaseDataFeed`
```python
class BaseDataFeed(Protocol):
    name: ClassVar[str]
    supports_intraday: ClassVar[bool] = False
    supports_adjusted: ClassVar[bool] = True

    async def fetch_daily_bars(self, ticker: str, exchange: str, frm: date, to: date) -> AsyncIterator[BarRow]: ...
    async def fetch_instrument_meta(self, ticker: str, exchange: str) -> InstrumentMeta | None: ...
    async def close(self) -> None: ...
```

`YFinanceFeed` implementation maps NSE→`.NS`, BSE→`.BO`; wraps `yfinance.download` in `asyncio.to_thread`; returns rows with `adj_factor = adj_close / close`.

### 5.2 `SecretsBackend`
```python
class SecretsBackend(Protocol):
    backend_name: ClassVar[str]
    async def get(self, ref: str) -> str: ...
    async def put(self, ref: str, value: str) -> None: ...
    async def rotate(self, ref: str, new_value: str) -> None: ...
    async def delete(self, ref: str) -> None: ...
```

`LocalEncryptedBackend` — Fernet over a JSON blob. Master key derived via `PBKDF2HMAC` from `CHRONOSYNC_VAULT_KEY`. File mode 0600. `rotate` keeps a `rotated_at` field in the JSON for audit.

### 5.3 Repositories
Functional style (module-level async functions taking `AsyncSession`), not classes, to keep call sites explicit.

```python
async def list_active_instruments(session, exchange: str | None = None) -> list[Instrument]: ...
async def upsert_daily_bars(session, rows: Sequence[BarRow], instrument_id: UUID, feed_name: str) -> int: ...
async def last_synced_map(session, feed_name: str) -> dict[UUID, date | None]: ...
async def mark_sync_success(session, instrument_id, feed_name, last_ts: date) -> None: ...
async def mark_sync_error(session, instrument_id, feed_name, error: str) -> None: ...
```

---

## 6. Sequence Flows

### 6.1 Daemon startup
```
Settings.load() → logging.configure()
   → DB.engine.create(pool=…)
   → DB.ping() (fail-fast)
   → ProviderRegistry.bootstrap(settings)
   → SecretsBackend.factory(settings.vault)
   → Scheduler.add_job(eod_cron, run_sync_iteration)
   → Scheduler.add_job(seed_cron, run_seed_iteration)
   → Scheduler.start()
   → install signal handlers → asyncio.Event.wait()
```

### 6.2 `run_sync_iteration`
```
session = engine.session()
exchanges = settings.sync.exchanges
for ex in exchanges:
    tasks = await planner.build_tasks(session, exchange=ex, feed=default_feed)
    await worker.run(tasks, concurrency=settings.providers.yfinance_concurrency)
log iteration summary
```

### 6.3 `worker.run`
```
sem = asyncio.Semaphore(concurrency)
async def handle(task):
    async with sem:
        try:
            rows = [r async for r in feed.fetch_daily_bars(task.ticker, task.exchange, task.from_date, task.to_date)]
            if rows:
                async with engine.session() as s:
                    await repos.bars.upsert_daily(s, rows, task.instrument_id, task.feed_name)
                    await repos.sync_state.mark_sync_success(s, task.instrument_id, task.feed_name, rows[-1].ts)
        except RetriableProviderError as e:
            # already exhausted retries by tenacity wrapper inside feed
            async with engine.session() as s:
                await repos.sync_state.mark_sync_error(s, task.instrument_id, task.feed_name, str(e))
await asyncio.gather(*(handle(t) for t in tasks), return_exceptions=False)
```

### 6.4 Admin `cred set` (CLI)
```
typer parses --account --key --value
backend = SecretsBackend.factory(settings.vault)
ref = f"{vendor}/{account_id}/{key_name}"
await backend.put(ref, value)
async with engine.session() as s:
    await repos.creds.upsert(s, account_id, key_name, ref)
print("OK")  # value never echoed
```

### 6.5 Read API: `GET /bars/daily`
```
deps.session → repos.bars.range(instrument_id, from, to)
if adjusted: project close * adj_factor as adj_close in response model
serialize via pydantic; paginate by (ts) cursor
```

---

## 7. Error Taxonomy (`chronosync.exceptions`)

```
ChronoSyncError
├── ConfigurationError
├── DatabaseError
│   ├── DatabaseUnavailable
│   └── UpsertConflict (should not occur; defensive)
├── ProviderError
│   ├── RetriableProviderError (429, 5xx, network)
│   └── PermanentProviderError (404 ticker, bad symbol)
├── SeederError
│   └── BhavcopyFetchError
├── SecretsError
│   ├── VaultUnlocked
│   └── SecretNotFound
└── PlannerError
```

Tenacity retries only `RetriableProviderError` and lower-level network exceptions. `PermanentProviderError` is recorded once and the task is dropped.

---

## 8. Logging (`chronosync.logging`)

`structlog` processors: `add_log_level`, `add_timestamper`, `EventRenamer("msg")`, custom `redact_secrets` (regex-matches `api_key|password|token|secret`), JSON renderer (prod) or `ConsoleRenderer` (dev). Rotating file handler from stdlib `logging.handlers.RotatingFileHandler` wired into structlog via the standard-library bridge.

Iteration summary keys: `event="sync_iteration_done"`, `exchange`, `scanned`, `fetched`, `upserted`, `errored`, `duration_s`.

---

## 9. CLI surface (`chronosync.cli`)

`typer` app structure:
```
chronosync
├── run                         # start daemon
│   └── --once                  # one iteration then exit (cron-friendly)
├── seed
│   └── --exchange (multi)
├── backfill
│   ├── --from YYYY-MM-DD
│   ├── --to   YYYY-MM-DD
│   └── --ticker (multi, optional)
├── status
│   └── --json
└── admin
    ├── user
    │   ├── add --email --display-name
    │   ├── list
    │   └── disable --id
    ├── account
    │   ├── add --user-id --vendor --label
    │   ├── list --user-id
    │   └── remove --id
    └── cred
        ├── set --account-id --key --value
        ├── rotate --account-id --key --value
        └── delete --account-id --key
```

`--value` accepts `-` to read from stdin so secrets aren't in shell history.

---

## 10. Read API Contract (`chronosync.api`)

Pydantic response models live in `chronosync.api.schemas` and are re-exported by `chronosync_client.models` so the client and server share types.

| Endpoint | Response model | Notes |
| --- | --- | --- |
| `GET /healthz` | `{status, db_ok, last_sync_at}` | 503 if `last_sync_at` older than `health_max_staleness_h` |
| `GET /instruments` | `Page[InstrumentDTO]` | filters: `exchange`, `active`, `q` (ticker prefix) |
| `GET /instruments/{id_or_ticker}` | `InstrumentDTO` | resolves UUID or ticker; ambiguous → 409 |
| `GET /bars/daily` | `Page[DailyBarDTO]` | filters: `instrument` (UUID or ticker), `from`, `to`, `adjusted` |
| `GET /sync/status` | `Page[SyncStatusDTO]` | filters: `instrument`, `feed_name` |

Pagination: cursor-based on the natural sort key (`(ts asc)` for bars, `(ticker asc)` for instruments). Cursor is opaque base64 of the last seen sort tuple.

---

## 11. Client SDK (`chronosync_client`)

```python
class ChronoSyncClient:
    def __init__(self, base_url: str, *, timeout: float = 10.0): ...
    async def list_instruments(self, *, exchange=None, active=None, q=None) -> AsyncIterator[InstrumentDTO]: ...
    async def get_instrument(self, id_or_ticker: str) -> InstrumentDTO: ...
    async def daily_bars(self, instrument: str, frm: date, to: date, *, adjusted=True) -> AsyncIterator[DailyBarDTO]: ...
    async def sync_status(self, instrument: str | None = None) -> AsyncIterator[SyncStatusDTO]: ...
    async def close(self) -> None: ...
```

Auto-paginates via the cursor. Pure-async; consumers wrap with `asyncio.run` or use within their own event loop.

---

## 12. Test Strategy

### 12.1 Unit tests (`tests/unit/`)
Pure, fast (<1s each), no I/O. Run in CI on every commit.

| File | Covers | Technique |
| --- | --- | --- |
| `test_config.py` | Settings parsing, env precedence, defaults | env mocking |
| `test_calendars.py` | NSE/BSE trading days, holidays, weekend skip | parametrize; freeze pseudo-clock |
| `test_planner.py` | Delta math: empty state, partial state, gap, weekend, future date | fake repo returns |
| `test_repositories_instruments.py` | Upsert semantics, filters | in-memory SQLite via `aiosqlite` for query shapes (where compatible) |
| `test_repositories_bars.py` | Row mapping, range filtering | same as above |
| `test_secrets_local.py` | Encrypt/decrypt round-trip, wrong key, file perms, rotate | tmp_path |
| `test_provider_yfinance.py` | Adapter shape, adjusted/raw math, error mapping | `vcrpy` cassette + monkeypatched `yfinance` |
| `test_seeder_bhavcopy.py` | ZIP parsing, deactivation logic | fixture ZIP files in `tests/unit/fixtures/` |

Coverage target: ≥80% on `chronosync.sync`, `chronosync.providers`, `chronosync.secrets`.

### 12.2 Functional tests (`tests/functional/`)
Slow (seconds), bring up real TimescaleDB via `testcontainers`. Skipped if Docker unavailable. Run in CI on PRs.

| File | Covers |
| --- | --- |
| `test_sync_e2e.py` | Seed → daemon `--once` → assert `daily_bars` populated for sample tickers (yfinance stubbed via vcrpy cassettes; container is real DB) |
| `test_backfill_idempotent.py` | Run backfill twice on same window; row count identical; no constraint violations |
| `test_api_contract.py` | Spin up FastAPI with TestClient; assert response shapes match `chronosync_client.models` |
| `test_cli_admin.py` | `admin user add` → `account add` → `cred set` → verify vault file contents encrypted + DB has only `secret_ref` |

### 12.3 Test infrastructure (`tests/conftest.py`)
- `event_loop` fixture: session-scoped asyncio loop.
- `pg_container` (session): `testcontainers.PostgresContainer("timescale/timescaledb:latest-pg15")`; runs Alembic on startup.
- `db_session` (function): begins a SAVEPOINT, rolls back on teardown — every test sees a clean DB.
- `tmp_vault` (function): `LocalEncryptedBackend` with `tmp_path` and a fixed test key.
- `vcr_cassette_dir`: `tests/{kind}/cassettes/{module}/`.

### 12.4 Marks
```
@pytest.mark.unit
@pytest.mark.functional
@pytest.mark.requires_docker
@pytest.mark.requires_network   # any test hitting live HTTP without a cassette (none in v1)
```

`pytest -m unit` runs the fast suite locally; CI runs `-m "unit or functional"`.

---

## 13. Migration Plan (Alembic)

Single initial revision `0001_initial.py` issues the DDL from §2 verbatim via `op.execute()` for Timescale-specific statements (`create_hypertable`, compression, retention) and `op.create_table` for regular tables. Downgrade is best-effort (`DROP TABLE ... CASCADE`).

Future migrations:
- `0002_intraday_fetcher.py` — no DDL; placeholder for when intraday lands (might add an index).
- `0003_portfolios.py` — adds `positions`, `orders`, etc. when phase 3 begins.

---

## 14. Performance Notes

- `repos.bars.upsert_daily` uses `insert(...).values(rows).on_conflict_do_update(...)` in one round-trip per task (typically ≤30 rows per backfill batch).
- `planner.build_tasks` issues two queries total per exchange: `list_active_instruments` + `last_synced_map`; no N+1.
- For backfills spanning years, planner emits multiple `FetchTask`s per instrument bounded by `settings.sync.backfill_batch_days` so a single failure rewinds at most one batch.
- API cursor pagination prevents OFFSET scans on large bar ranges.

---

## 15. Open LLD-level Questions

These should be closed before code freeze, but won't block scaffolding:

- **Secrets default**: file-based Fernet vs OS keyring as the "out-of-the-box" choice. File-based wins for Docker portability; keyring wins for OS-integrated UX. Plan: ship both, default to file-based, document the swap.
- **Bhavcopy URL templates**: NSE has migrated archive URLs ~yearly. Plan: pin templates in `chronosync/seeders/_urls.py`, override via env, add a regression test that re-fetches a known historical date.
- **yfinance vs yfinance-cache**: caching layer would reduce repeat fetches during dev; can layer in `providers/cache.py` later — not v1.
