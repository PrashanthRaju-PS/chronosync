# ChronoSync — High-Level Design (HLD)

**Status:** Draft v0.1 · **Companion to:** [Requirements.md](../Requirements.md) · **Drilled down in:** [LLD.md](LLD.md)

---

## 1. Design Q&A Summary

Captured to make the locked decisions auditable. See `Requirements.md` §3 for the canonical table.

| Q | Answer | → Decision |
| --- | --- | --- |
| DB engine for ~1000+ concurrent requests, multi-geo? | Postgres + TimescaleDB | D1 |
| Daemon model? | Long-running asyncio | D2, D3 |
| First provider? | yfinance | D4 |
| Adjusted vs raw OHLC? | Raw + adjustment factor | D5 |
| `market_cap` placement? | On `instruments` (+ as_of) | D6 |
| Intraday scope? | Reserved schema, deferred fetcher | D7, D14 |
| Sync trigger? | Post-EOD + backfill + incremental | D8 |
| Instrument seed source? | NSE/BSE Bhavcopy | D9 |
| Trading calendar? | `pandas_market_calendars` | D10 |
| Secrets / multi-user? | Pluggable vault; users/accounts reserved | D11, D12 |
| Deploy target? | Win-dev → Linux/Docker | D16 |
| Query interface? | Read-only FastAPI + Python client; admin CLI for writes | D13 |
| Tooling? | uv + pyproject.toml | D15 |

---

## 2. System Context

```
                          ┌───────────────────────────────────┐
                          │      External Data Providers      │
                          │  yfinance · NSE Bhavcopy · …      │
                          └────────────┬──────────────────────┘
                                       │ HTTP (retry/backoff)
                                       ▼
┌──────────────┐   admin CLI   ┌───────────────────────────────┐   asyncpg   ┌────────────────┐
│   Operator   │──────────────▶│        ChronoSync Daemon       │────────────▶│  TimescaleDB    │
└──────────────┘               │  (asyncio · sync engine ·      │   (upsert)  │  instruments    │
                               │   seeders · provider registry) │             │  daily_bars     │
                               └────────────┬──────────────────┘             │  intraday_bars  │
                                            │                                │  sync_state     │
                                            │ shares DB engine               │  users/accounts │
                                            ▼                                │  vendor_creds   │
                               ┌───────────────────────────────┐             └────────────────┘
                               │     FastAPI Read API           │                     ▲
                               │  /instruments /bars/daily      │                     │ SELECT
                               │  /sync/status /healthz         │                     │
                               └────────────┬──────────────────┘                     │
                                            │ HTTP                                    │
                                            ▼                                         │
                               ┌───────────────────────────────┐                     │
                               │  chronosync_client (Python)    │─────────────────────┘
                               │  consumed by: Strategies,      │
                               │  KiteAutoSync, notebooks, …    │
                               └───────────────────────────────┘

                               ┌───────────────────────────────┐
                               │      Secrets Vault             │
                               │  (local-encrypted | keyring |  │
                               │   HashiCorp Vault)             │
                               └───────────────────────────────┘
                                            ▲
                                            │ writes only via admin CLI
                                            │ reads by daemon at sync time
                                            │ DB stores only secret_ref strings
```

---

## 3. Components

### 3.1 Daemon (`chronosync.daemon`)
The long-running supervisor. Owns the asyncio event loop, signal handling (SIGINT/SIGTERM), and a heartbeat. Schedules sync iterations via a configurable cron expression (post-EOD per exchange) and exposes a `--once` mode for cron-style external triggers if ever needed.

### 3.2 Sync engine (`chronosync.sync`)
Three sub-modules:
- **`planner`** — given the active instrument universe + current `sync_state` + the exchange calendar, emits a list of `FetchTask(instrument_id, feed_name, from_date, to_date)`.
- **`worker`** — bounded-concurrency consumer (`asyncio.Semaphore`) that runs `FetchTask`s against the provider registry, upserts to DB, updates `sync_state`.
- **`state`** — read/write for the `sync_state` table.

### 3.3 Provider layer (`chronosync.providers`)
- `BaseDataFeed` (ABC) — `fetch_daily_bars(symbol, from, to) -> AsyncIterator[BarRow]`, `fetch_instrument_meta(symbol)`, capability flags.
- `YFinanceFeed` — first concrete; wraps `yfinance.Ticker.history` behind an async-thread-pool shim.
- `registry` — name→class lookup; selected via config.

### 3.4 Seeders (`chronosync.seeders`)
- `BaseSeeder` ABC.
- `NSEBhavcopySeeder`, `BSEBhavcopySeeder` — downloads daily archives, parses, upserts `instruments`. Configurable URL templates (Bhavcopy URLs historically change).

### 3.5 Calendars (`chronosync.calendars`)
Thin wrapper over `pandas_market_calendars`. Caches per-exchange schedules in memory; offers `trading_days(exchange, from, to)` and `is_trading_day(exchange, date)`.

### 3.6 Persistence (`chronosync.db`)
- `engine.py` — async engine factory, session context manager.
- `models.py` — SQLAlchemy 2.x declarative models.
- `repositories.py` — narrow query API per aggregate (instruments, bars, sync_state, users, accounts, credentials). No business logic; just typed CRUD + upsert helpers.

### 3.7 Secrets (`chronosync.secrets`)
- `SecretsBackend` ABC: `get(ref)`, `put(ref, value)`, `delete(ref)`, `rotate(ref, new_value)`.
- `LocalEncryptedBackend` — Fernet-encrypted JSON file at `$CHRONOSYNC_VAULT_PATH`, key derived from a master password env var.
- `KeyringBackend` — OS keyring (alt).
- `VaultBackend` — stub raising `NotImplementedError` with TODO.

Vault paths are opaque strings (`vendor/{account_id}/{key_name}`) stored in `vendor_credentials.secret_ref`. Raw secrets never enter Postgres or logs.

### 3.8 Read API (`chronosync.api`)
FastAPI app with read-only routers. Shares the DB engine with the daemon when colocated; otherwise can run as a separate process pointing at the same DB. Binds to `127.0.0.1` by default.

### 3.9 CLI (`chronosync.cli`)
`typer`-based. Top-level commands: `run`, `seed`, `backfill`, `status`, `admin {user,account,cred}`. The admin subtree is the only path that writes to the vault.

### 3.10 Client SDK (`chronosync_client`)
Separate top-level package, installable independently (`pip install chronosync[client]`). Thin `httpx`-based wrapper over the read API with `pydantic` response models mirroring server schemas.

---

## 4. Data Flow

### 4.1 Daily sync iteration (happy path)
```
scheduler tick (e.g., 18:30 IST)
   │
   ▼
planner.build_tasks()
   ├── repos.instruments.list_active()
   ├── repos.sync_state.last_synced_map()
   ├── calendars.trading_days(exchange, last+1, today)
   └── → [FetchTask, …]
   │
   ▼
worker.run(tasks, concurrency=N)
   ├── provider.fetch_daily_bars(symbol, from, to)        ← retry/backoff
   ├── repos.bars.upsert_daily(rows)                       ← ON CONFLICT
   └── repos.sync_state.mark_success(instrument, last_ts)
   │
   ▼
logger.info("sync_iteration_done", scanned=…, fetched=…, upserted=…, errored=…)
```

### 4.2 Failure paths
- **Provider 429/5xx** → tenacity retries; on terminal failure → `sync_state.mark_error(instrument, error)`; iteration continues.
- **DB unavailable** → planner aborts iteration with a logged error; next tick retries.
- **Daemon crash** → systemd/Docker restarts; on boot, `sync_state` already reflects last successful ts per instrument; no double-write because of idempotent upserts.

### 4.3 Backfill
CLI `backfill --from … --to … [--ticker …]` short-circuits the scheduler and submits an ad-hoc planner run with a fixed date window. Same worker pipeline.

### 4.4 Seeding
CLI `seed --exchange NSE` runs `NSEBhavcopySeeder.run()` once; daemon also runs it on a separate cron tick (e.g., daily 09:00 IST to catch new listings before next EOD).

---

## 5. Concurrency & Performance

- Single asyncio event loop. CPU-bound parsing (pandas) wrapped in `asyncio.to_thread`.
- `asyncio.Semaphore(N)` bounds provider concurrency; `N` configured per provider (`yfinance_concurrency=8` default).
- DB writes batched via SQLAlchemy `executemany`-style `insert(...).on_conflict_do_update(...)`.
- Memory: never load >1 instrument's full history into memory; planner produces tasks streamingly, worker upserts each task's rows then frees.

---

## 6. Persistence Topology

- One TimescaleDB instance. Two hypertables (`daily_bars`, `intraday_bars`) on `ts`. Regular tables for `instruments`, `sync_state`, `users`, `accounts`, `vendor_credentials`.
- Compression policies declared in migration: `daily_bars` compress after 90d, `intraday_bars` after 7d.
- Connection pool: `min=2, max=20` from daemon; `min=2, max=10` from API. Both sized for a 100-conn Postgres.

---

## 7. Security

- Read API: no auth in v1, default bind `127.0.0.1:8088`. Document explicit step to expose externally + add auth.
- Vault: master key from `CHRONOSYNC_VAULT_KEY` env (or OS keyring). Never logged. Vault file mode `0600`.
- DB: connection string via env. `vendor_credentials.secret_ref` is opaque.
- Logs: redaction filter in `chronosync.logging` strips known secret keys (`api_key`, `password`, `token`, `secret`).

---

## 8. Deployment

### 8.1 Local development (Win11)
- `uv sync` installs deps + creates venv.
- `docker compose up timescaledb` starts the database.
- `uv run alembic upgrade head` applies migrations.
- `uv run chronosync seed --exchange NSE` populates instruments.
- `uv run chronosync run` starts daemon. `uv run uvicorn chronosync.api.app:app` starts read API.

### 8.2 Production (Linux/Docker)
- `docker-compose.yml` defines `timescaledb`, `chronosync-daemon`, `chronosync-api` services.
- Daemon and API share the volume-mounted vault directory.
- Migrations run as a one-shot job before daemon boot.
- Logs to stdout (JSON); host's log driver handles rotation.

---

## 9. Observability

- **Logs:** `structlog` JSON in prod, console in dev. Iteration summary line at INFO.
- **Metrics (future):** OpenTelemetry exporter behind a feature flag — Counter for upserts, Histogram for fetch latency, Gauge for queue depth. Out of scope v1.
- **Health:** `/healthz` checks DB ping + last successful sync recency. Returns 503 if no sync in >24h (configurable).

---

## 10. Extensibility Roadmap

| Phase | Adds | Touches |
| --- | --- | --- |
| v1 | Daily NSE/BSE via yfinance + read API + CLI | scoped above |
| v1.1 | Bhavcopy adjusted-close cross-check; market_cap refresh job | seeders + sync |
| v2 | Intraday fetcher (1-min) | providers + sync (no schema change) |
| v2.1 | Additional providers (Kite, Breeze) | providers + secrets |
| v3 | Per-user portfolios; positions tables; authenticated admin API | db.models + new api package |
| v4 | Global markets (NYSE, LSE); FX, futures, options | calendars + providers; asset_class enum already reserved |

---

## 11. Risks & Mitigations

| Risk | Mitigation |
| --- | --- |
| Bhavcopy URLs change | URL templates in config; override via env; integration test runs daily |
| yfinance rate limits / blocks | Bounded concurrency + tenacity backoff; circuit-break + fallback provider hook |
| Timescale upgrade pain in Docker | Pin major; document upgrade SOP in README |
| Vault master-key loss = data loss | Document recovery; encourage backup of vault file + key separately |
| Schema drift between daemon and API | Single source of truth in `chronosync.db.models`; both processes import it |
