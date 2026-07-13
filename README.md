# ChronoSync

Provider-agnostic historical Price–Volume database daemon. Starts with NSE/BSE equities via yfinance, scales to global markets, futures, options, and fixed income.

See:
- [Requirements.md](Requirements.md) — authoritative SRS
- [docs/HLD.md](docs/HLD.md) — system context, components, data flows
- [docs/LLD.md](docs/LLD.md) — module-level design, DDL, sequence flows, test plan

---

## Quickstart (Docker — recommended)

Runs the whole stack — TimescaleDB, migrations, the sync daemon, and the read
API — in containers. They carry `restart: unless-stopped`, so they auto-start on
boot (Docker Desktop) and stop gracefully on shutdown (SIGTERM → the daemon
stops its scheduler and closes DB connections cleanly).

```bash
cp .env.example .env            # then set CHRONOSYNC_VAULT_KEY to a strong passphrase
docker compose up -d --build
```

This brings up `chronosync-db`, runs `chronosync-migrate` once (`alembic upgrade
head`), then starts `chronosync-daemon` and `chronosync-api`. The read API is
published on `127.0.0.1:8088`.

```bash
docker compose ps                 # status
docker compose logs -f daemon     # follow the sync daemon
docker compose down               # stop the whole stack
```

One-off seed / backfill run inside the daemon container:
```bash
docker compose exec daemon uv run chronosync seed --exchange NSE
docker compose exec daemon uv run chronosync backfill --from 2025-06-01 --to 2026-05-31 --ticker RELIANCE
```

Query:
```bash
curl "http://127.0.0.1:8088/instruments/RELIANCE"
curl "http://127.0.0.1:8088/bars/daily?instrument=RELIANCE&from=2026-01-01&to=2026-05-31"
```

> The app containers override the DB DSN to the compose network
> (`timescaledb:5432`); the `localhost` DSN in `.env` is for the bare-metal dev
> mode below.

## Dev (bare-metal app, Dockerized DB)

Run the app processes directly on the host for faster iteration, with only the
DB in Docker.

```powershell
# Install uv once: https://docs.astral.sh/uv/getting-started/installation/
uv sync --extra dev
docker compose up -d timescaledb        # DB only
copy .env.example .env                  # set CHRONOSYNC_VAULT_KEY
uv run alembic upgrade head
uv run chronosync seed --exchange NSE
uv run chronosync run                   # daemon (long-running)
# separate shell:
uv run uvicorn chronosync.api.app:app --host 127.0.0.1 --port 8088
```

The helper scripts `start-chronosync.ps1` / `stop-chronosync.ps1` launch and
reap both processes (stop sweeps up orphaned child processes too).

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
