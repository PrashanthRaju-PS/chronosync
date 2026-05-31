# ChronoSync

Provider-agnostic historical Price–Volume database daemon. Starts with NSE/BSE equities via yfinance, scales to global markets, futures, options, and fixed income.

See:
- [Requirements.md](Requirements.md) — authoritative SRS
- [docs/HLD.md](docs/HLD.md) — system context, components, data flows
- [docs/LLD.md](docs/LLD.md) — module-level design, DDL, sequence flows, test plan

---

## Quickstart (Windows dev)

```powershell
# 1. Install uv (one time)
#    https://docs.astral.sh/uv/getting-started/installation/

# 2. Install deps
uv sync --extra dev

# 3. Bring up TimescaleDB
docker compose up -d timescaledb

# 4. Configure
copy .env.example .env
#  - set CHRONOSYNC_VAULT_KEY to a strong passphrase

# 5. Run migrations
uv run alembic upgrade head

# 6. Seed the instrument universe (NSE Bhavcopy)
uv run chronosync seed --exchange NSE

# 7. Backfill a year for one ticker
uv run chronosync backfill --from 2025-06-01 --to 2026-05-31 --ticker RELIANCE

# 8. Start the daemon (long-running)
uv run chronosync run

# 9. (Separate shell) Start the read API
uv run uvicorn chronosync.api.app:app --host 127.0.0.1 --port 8088
```

Query:
```bash
curl "http://127.0.0.1:8088/instruments/RELIANCE"
curl "http://127.0.0.1:8088/bars/daily?instrument=RELIANCE&from=2026-01-01&to=2026-05-31"
```

## Quickstart (Linux/Docker deploy)

```bash
cp .env.example .env
# edit .env — set CHRONOSYNC_VAULT_KEY
docker compose --profile deploy up -d
```

Brings up TimescaleDB, runs migrations once, starts the daemon and the read API.

## Admin (users / accounts / credentials)

```bash
uv run chronosync admin user add prashu.ps@gmail.com --display-name "Prashanth"
uv run chronosync admin user list

uv run chronosync admin account add --user-id <UUID> --vendor kite --label main
uv run chronosync admin account list --user-id <UUID>

# Pipe the secret via stdin so it never lands in shell history:
echo -n 'my-api-key' | uv run chronosync admin cred set --account-id <UUID> --key api_key --value -
```

The vault stores the encrypted value; the DB only sees an opaque `secret_ref`.

## Testing

```powershell
# Fast feedback — pure unit tests, no Docker
uv run pytest -m unit

# Full functional suite (spins up a TimescaleDB container)
uv run pytest -m "unit or functional"

# Static checks
uv run ruff check .
uv run mypy chronosync
```

## Architecture (one-liner)

`Daemon (asyncio scheduler) → Planner (deltas vs sync_state) → Worker (bounded-concurrency provider fetch + idempotent upsert) → TimescaleDB hypertables`. Read API is a separate process pointing at the same DB. Secrets live in a pluggable vault; the DB only stores opaque references. Full picture in [docs/HLD.md](docs/HLD.md).

## Extending

- **Add a provider:** implement `BaseDataFeed` ([chronosync/providers/base.py](chronosync/providers/base.py)) and register in [chronosync/providers/registry.py](chronosync/providers/registry.py).
- **Add an exchange:** ensure `pandas_market_calendars` supports it; if needed, add a mapping in [chronosync/calendars.py](chronosync/calendars.py).
- **Swap secrets backend:** point `CHRONOSYNC_VAULT__BACKEND` at `keyring` or `hashicorp` (latter is a stub — wire to `hvac`).

## Project layout

See [docs/LLD.md §1](docs/LLD.md) for the full tree. Key entry points:
- Daemon: [chronosync/daemon.py](chronosync/daemon.py)
- CLI: [chronosync/cli.py](chronosync/cli.py)
- Read API: [chronosync/api/app.py](chronosync/api/app.py)
- Client SDK: [chronosync_client/client.py](chronosync_client/client.py)
