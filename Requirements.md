# ChronoSync — Requirements Specification

**Status:** Draft v0.1 · **Owner:** Prashanth Raju · **Last updated:** 2026-05-31

Authoritative SRS. Supersedes the original prompt (preserved at [docs/00_OriginalPrompt.md](docs/00_OriginalPrompt.md)). Decisions locked in this document trace back to the design Q&A captured in [docs/HLD.md](docs/HLD.md) §1.

---

## 1. Purpose & Scope

ChronoSync is a long-running Python daemon that maintains an efficient, scalable historical **Price–Volume** database for tradable instruments. It is provider-agnostic and starts with **Indian Equities (NSE/BSE)**, but its schema and abstractions are designed to extend to global markets, futures, options, and fixed-income assets.

### 1.1 In scope (v1)
- Daily OHLCV ingestion for NSE/BSE equities via `yfinance`.
- Instrument universe seeding from official NSE/BSE Bhavcopy archives.
- Post-EOD scheduled sync with backfill + incremental modes.
- TimescaleDB persistence with hypertables, upserts, and retention hooks.
- Read-only FastAPI query API + thin Python client (`chronosync_client`).
- Admin CLI for user/account/credential mutations (vault-backed).
- Pluggable secrets backend; local encrypted store as default.

### 1.2 Out of scope (v1, but schema-reserved)
- Intraday bar ingestion (table exists; fetcher deferred).
- Futures, options, fixed income (asset class enum reserved).
- Authenticated write API / web admin UI.
- Multi-tenant data partitioning (market data is shared; user/account tables stubbed for the portfolio layer that lands later).
- Real-time streaming (tick/L2).

### 1.3 Non-goals
- Order management, broker execution, portfolio analytics. ChronoSync only stores reference + historical bar data. Trading apps (Strategies, KiteAutoSync) consume via the read API.

---

## 2. Stakeholders & Users

| Role | Interaction |
| --- | --- |
| Operator (you) | Runs daemon, manages users/accounts/credentials via CLI |
| Downstream apps | Query bars/instruments via FastAPI or Python client |
| Future end users | Their broker credentials live in the vault; portfolios join market data (later phase) |

---

## 3. Locked Design Decisions

These are the result of the design Q&A and override any conflicting language in the original prompt.

| # | Decision | Rationale |
| --- | --- | --- |
| D1 | **PostgreSQL + TimescaleDB** (not SQLite/DuckDB) | Targets 1000+ concurrent requests, multi-geography, multi-asset scale; hypertables + compression are load-bearing. |
| D2 | **Long-running asyncio daemon** (not cron-driven worker) | Single process owns scheduling, retries, and state; simpler lifecycle on Win-dev and Linux/Docker-prod. |
| D3 | **`asyncio` concurrency** (no threading) | One concurrency model; `asyncpg` + `httpx` are async-native. |
| D4 | **`yfinance` is the first concrete `BaseDataFeed`** | Free, broad coverage, returns adjusted + raw OHLC. |
| D5 | **Store raw OHLC + adjustment factor** | Provider-agnostic; never lose the unadjusted truth. |
| D6 | **`market_cap` on `instruments`** + `market_cap_as_of` timestamp | Snapshot column with explicit staleness marker; refresh job runs separately. |
| D7 | **Intraday in scope; daily implemented v1** | `intraday_bars` hypertable created via migration; no fetcher in v1. |
| D8 | **Post-EOD trigger; backfill + incremental sync modes** | Daemon computes per-instrument deltas at scheduled time + on demand. |
| D9 | **Bhavcopy from NSE/BSE official archives** for instrument seeding | yfinance has no master-list API; Bhavcopy is authoritative and free. |
| D10 | **`pandas_market_calendars`** for trading-day math | Has NSE/BSE built in; supports global exchanges later. |
| D11 | **Shared market data; per-user portfolios reserved** | `instruments`/`bars` are global; `users`/`accounts`/`vendor_credentials` tables created empty for the portfolio phase. |
| D12 | **Pluggable `SecretsBackend`; default = local encrypted (age/SOPS or keyring)**; HashiCorp Vault stubbed | Win-dev today, Vault when deployed. No vendor lock-in. |
| D13 | **Read-only FastAPI** + admin CLI for writes | No internet-exposed write surface in v1. |
| D14 | **Separate `daily_bars` and `intraday_bars` hypertables** | Independent retention, compression, drop policies. |
| D15 | **`uv` + `pyproject.toml`** | Fast, single lockfile, good Win support. |
| D16 | **Deployment: Win for now, Linux/Docker eventually** | Compose file authored for both; no Win-specific paths in code. |

---

## 4. Functional Requirements

### 4.1 Instrument universe management
- **FR-1.1** Seed `instruments` from a configurable list of exchanges (NSE, BSE in v1).
- **FR-1.2** Daily Bhavcopy ingestion updates `is_active`, adds new listings, marks delisted.
- **FR-1.3** Each instrument carries: `id` (UUID), `ticker`, `exchange`, `asset_class`, `country_code`, `currency`, `isin`, `market_cap`, `market_cap_as_of`, `is_active`, timestamps.
- **FR-1.4** `(ticker, exchange)` is unique; lookups by ticker are indexed.

### 4.2 Daily bar ingestion
- **FR-2.1** At scheduled EOD trigger (configurable cron expression evaluated by the daemon), enumerate active instruments and compute missing date ranges per `(instrument_id, feed_name)` from `sync_state.last_synced_ts`.
- **FR-2.2** Fetch missing daily bars via the configured `BaseDataFeed` in bounded-concurrency batches (`asyncio.Semaphore`).
- **FR-2.3** Persist via `INSERT … ON CONFLICT (instrument_id, ts) DO UPDATE` — fully idempotent.
- **FR-2.4** Store both raw OHLC and `adj_factor` so adjusted close can be derived on read.
- **FR-2.5** Skip non-trading days using the exchange's `pandas_market_calendars` schedule.
- **FR-2.6** Support manual backfill via CLI: `chronosync backfill --from … --to … [--ticker …]`.

### 4.3 Resilience
- **FR-3.1** All outbound HTTP calls retry with exponential backoff (tenacity): 429, 5xx, network errors. Configurable max attempts, base delay, max delay.
- **FR-3.2** Per-instrument failures are recorded in `sync_state.last_error` without aborting the batch.
- **FR-3.3** Daemon survives transient DB outages; in-flight sync iteration aborts cleanly, next iteration retries.

### 4.4 Read API
- **FR-4.1** `GET /healthz` — liveness.
- **FR-4.2** `GET /instruments?exchange=&active=&q=` — paginated.
- **FR-4.3** `GET /instruments/{id_or_ticker}` — single lookup.
- **FR-4.4** `GET /bars/daily?instrument=&from=&to=&adjusted=` — paginated; `adjusted=true` applies `adj_factor`.
- **FR-4.5** `GET /sync/status?instrument=` — last sync time, errors, attempt counts.
- **FR-4.6** All endpoints are read-only; no auth in v1 (assumed loopback / internal network).

### 4.5 Admin CLI
- **FR-5.1** `chronosync run` — start daemon.
- **FR-5.2** `chronosync seed [--exchange NSE,BSE]` — one-shot Bhavcopy ingest.
- **FR-5.3** `chronosync backfill --from --to [--ticker]` — historical fill.
- **FR-5.4** `chronosync status` — sync state summary.
- **FR-5.5** `chronosync admin user add|list|disable` — user lifecycle.
- **FR-5.6** `chronosync admin account add|list|remove` — broker accounts under a user.
- **FR-5.7** `chronosync admin cred set|rotate|delete` — writes to vault, stores only `secret_ref` in DB.

### 4.6 Logging & observability
- **FR-6.1** Structured logs via `structlog` to stdout (JSON in prod, console-pretty in dev).
- **FR-6.2** Rotating file handler with size + retention configurable.
- **FR-6.3** Per-iteration sync summary: instruments scanned, fetched, upserted, errored, duration.

---

## 5. Non-Functional Requirements

| Category | Requirement |
| --- | --- |
| **Performance** | Sync 5,000 NSE+BSE equities daily within 10 minutes on a 4-core Docker host (concurrency-bounded by provider rate limits). Read API p95 ≤ 150 ms for single-instrument 1-year daily range on warm cache. |
| **Scalability** | Schema must support 50k+ instruments and 10 years of daily history (~180M rows) without query plan regressions. Intraday hypertable sized for 1-min bars × 5k instruments × 6 mo (~600M rows) when enabled. |
| **Reliability** | No data loss on daemon crash; resume from `sync_state`. Idempotent upserts guarantee re-runs are safe. |
| **Security** | Raw API keys never persisted in DB or logs. Vault is the only store; DB holds opaque `secret_ref` strings. Read API binds to loopback by default. |
| **Portability** | Pure-Python where possible; no Windows-specific paths. Runs under Python 3.11+ on Win11 and Linux. |
| **Maintainability** | Strict typing (`mypy --strict`), `ruff` clean, ≥70% line coverage on `chronosync.sync` and `chronosync.providers`. |
| **Memory** | Streaming/batched fetches; no provider call materializes >1 instrument-decade in memory. |

---

## 6. External Interfaces

- **Data providers:** yfinance (v1); `BaseDataFeed` ABC for future Kite/Breeze/Fyers/Alpha Vantage.
- **Seed source:** NSE/BSE Bhavcopy ZIP archives (HTTP).
- **Calendar:** `pandas_market_calendars`.
- **Secrets:** local encrypted file (default), keyring (alt), HashiCorp Vault (stub).
- **Database:** TimescaleDB 2.x on PostgreSQL 15+.
- **Consumers:** `chronosync_client` (Python) + raw HTTP.

---

## 7. Data Retention

- `daily_bars`: indefinite. Timescale compression after 90 days.
- `intraday_bars` (when enabled): 24-month retention, compression after 7 days.
- `sync_state`: rolling, only current row per `(instrument_id, feed_name)`; historical iterations logged to file only.

---

## 8. Acceptance Criteria (v1)

1. `docker compose up` starts TimescaleDB; `uv run chronosync seed --exchange NSE` populates `instruments` from latest Bhavcopy.
2. `uv run chronosync run` starts the daemon; at the next scheduled tick (or `--once` flag), daily bars for all active instruments are populated.
3. Re-running the daemon over the same date range produces zero new rows (idempotent).
4. `GET /bars/daily?instrument=RELIANCE&from=2026-01-01&to=2026-05-31` returns rows with both raw and adjusted close available.
5. `chronosync admin cred set` writes to the local encrypted vault; DB row contains only `secret_ref`.
6. Forcing a 429 from a mock provider results in retries with exponential backoff and a recorded `last_error` only on terminal failure.
7. `pytest` green, `ruff check` clean, `mypy --strict chronosync` clean.

---

## 9. Open Items (deferred)

- Choice of local encrypted backend: `age`-encrypted file vs OS `keyring` — LLD picks default; both implementations land in v1.
- Bhavcopy URL format historically changes; v1 ships best-known URL + override env vars.
- Concrete cron expression for EOD trigger per exchange (defaults to NSE 18:30 IST; configurable).
