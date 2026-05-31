"""Typer CLI. See docs/LLD.md §9."""

from __future__ import annotations

import asyncio
import sys
from datetime import date
from uuid import UUID

import typer
from rich.console import Console
from rich.table import Table

from chronosync.config import get_settings
from chronosync.daemon import Daemon, run_once
from chronosync.db import engine as db_engine
from chronosync.db import repositories as repos
from chronosync.logging import configure_logging
from chronosync.providers import registry as provider_registry
from chronosync.secrets import build_backend
from chronosync.seeders import BSEBhavcopySeeder, NSEBhavcopySeeder
from chronosync.sync import planner, worker

app = typer.Typer(no_args_is_help=True, add_completion=False, help="ChronoSync CLI")
admin_app = typer.Typer(no_args_is_help=True, help="Mutate users / accounts / credentials")
user_app = typer.Typer(no_args_is_help=True)
account_app = typer.Typer(no_args_is_help=True)
cred_app = typer.Typer(no_args_is_help=True)
admin_app.add_typer(user_app, name="user")
admin_app.add_typer(account_app, name="account")
admin_app.add_typer(cred_app, name="cred")
app.add_typer(admin_app, name="admin")

console = Console()


def _bootstrap() -> None:
    settings = get_settings()
    configure_logging(settings.logging)
    db_engine.init_engine(settings.db)


def _async(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


# ----- daemon control -----


@app.command("run")
def cmd_run(once: bool = typer.Option(False, "--once", help="One iteration then exit")) -> None:
    """Start the long-running daemon (or `--once` for a single sync iteration)."""
    if once:
        _async(run_once())
    else:
        _async(Daemon().start())


@app.command("seed")
def cmd_seed(exchange: list[str] = typer.Option(None, "--exchange", "-e", help="NSE, BSE")) -> None:
    """Run Bhavcopy seeders for the given exchanges (defaults to all configured)."""
    _bootstrap()
    settings = get_settings()
    targets = [e.upper() for e in (exchange or settings.sync.exchanges)]

    async def _run() -> None:
        await db_engine.ping()
        for ex in targets:
            seeder = NSEBhavcopySeeder() if ex == "NSE" else BSEBhavcopySeeder() if ex == "BSE" else None
            if seeder is None:
                console.print(f"[yellow]skipping unknown exchange[/]: {ex}")
                continue
            async with db_engine.session_scope() as s:
                n = await seeder.run(s)
            console.print(f"[green]seeded {ex}[/]: {n} rows touched")
        await db_engine.dispose()

    _async(_run())


@app.command("backfill")
def cmd_backfill(
    frm: str = typer.Option(..., "--from", help="YYYY-MM-DD"),
    to: str = typer.Option(..., "--to", help="YYYY-MM-DD"),
    ticker: list[str] = typer.Option(None, "--ticker", "-t", help="Limit to these tickers"),
    exchange: str = typer.Option("NSE", "--exchange", "-e"),
) -> None:
    """Force a historical fetch over a specific window."""
    _bootstrap()
    settings = get_settings()
    frm_d, to_d = date.fromisoformat(frm), date.fromisoformat(to)
    if frm_d > to_d:
        console.print("[red]--from must be on or before --to[/]")
        raise typer.Exit(2)

    async def _run() -> None:
        await db_engine.ping()
        provider_registry.bootstrap_registry(settings.providers)
        feed = provider_registry.get_registry().get(settings.providers.default_feed)

        async with db_engine.session_scope() as s:
            instruments = await repos.list_active_instruments(s, exchange=exchange)
        if ticker:
            ticker_upper = {t.upper() for t in ticker}
            instruments = [i for i in instruments if i.ticker.upper() in ticker_upper]
        if not instruments:
            console.print("[yellow]no matching active instruments[/]")
            await db_engine.dispose()
            return

        tasks: list[planner.FetchTask] = []
        for inst in instruments:
            tasks.extend(
                planner.split_backfill(
                    inst.id,
                    inst.ticker,
                    inst.exchange,
                    settings.providers.default_feed,
                    frm_d,
                    to_d,
                    batch_days=settings.sync.backfill_batch_days,
                )
            )
        summary = await worker.run(
            tasks,
            feed=feed,
            concurrency=settings.providers.yfinance_concurrency,
        )
        console.print(
            f"[green]backfill done[/]  scanned={summary.scanned} "
            f"fetched={summary.fetched} upserted={summary.upserted} errored={summary.errored} "
            f"duration_s={summary.duration_s}"
        )
        await provider_registry.get_registry().close_all()
        await db_engine.dispose()

    _async(_run())


@app.command("status")
def cmd_status(as_json: bool = typer.Option(False, "--json")) -> None:
    """Show sync-state summary."""
    _bootstrap()

    async def _run() -> None:
        await db_engine.ping()
        async with db_engine.session_scope() as s:
            rows = await repos.sync_status_for(s, limit=200)
        if as_json:
            import json

            console.print_json(
                json.dumps(
                    [
                        {
                            "instrument_id": str(r.instrument_id),
                            "feed": r.feed_name,
                            "last_synced_ts": r.last_synced_ts.isoformat() if r.last_synced_ts else None,
                            "last_error": r.last_error,
                            "attempts": r.attempt_count,
                        }
                        for r in rows
                    ]
                )
            )
        else:
            t = Table("instrument", "feed", "last_synced", "error", "attempts")
            for r in rows:
                t.add_row(
                    str(r.instrument_id),
                    r.feed_name,
                    r.last_synced_ts.isoformat() if r.last_synced_ts else "—",
                    (r.last_error or "")[:60],
                    str(r.attempt_count),
                )
            console.print(t)
        await db_engine.dispose()

    _async(_run())


# ----- admin: users -----


@user_app.command("add")
def admin_user_add(email: str, display_name: str = typer.Option(None, "--display-name")) -> None:
    _bootstrap()

    async def _run() -> None:
        async with db_engine.session_scope() as s:
            u = await repos.add_user(s, email=email, display_name=display_name)
        console.print(f"[green]user created[/]: {u.id}  {u.email}")
        await db_engine.dispose()

    _async(_run())


@user_app.command("list")
def admin_user_list() -> None:
    _bootstrap()

    async def _run() -> None:
        async with db_engine.session_scope() as s:
            users = await repos.list_users(s)
        t = Table("id", "email", "display_name", "active")
        for u in users:
            t.add_row(str(u.id), u.email, u.display_name or "", str(u.is_active))
        console.print(t)
        await db_engine.dispose()

    _async(_run())


@user_app.command("disable")
def admin_user_disable(user_id: str) -> None:
    _bootstrap()

    async def _run() -> None:
        async with db_engine.session_scope() as s:
            await repos.disable_user(s, UUID(user_id))
        console.print("[green]disabled[/]")
        await db_engine.dispose()

    _async(_run())


# ----- admin: accounts -----


@account_app.command("add")
def admin_account_add(
    user_id: str = typer.Option(..., "--user-id"),
    vendor: str = typer.Option(..., "--vendor"),
    label: str = typer.Option(..., "--label"),
) -> None:
    _bootstrap()

    async def _run() -> None:
        async with db_engine.session_scope() as s:
            a = await repos.add_account(s, user_id=UUID(user_id), vendor=vendor, label=label)
        console.print(f"[green]account created[/]: {a.id}  {a.vendor}/{a.account_label}")
        await db_engine.dispose()

    _async(_run())


@account_app.command("list")
def admin_account_list(user_id: str = typer.Option(None, "--user-id")) -> None:
    _bootstrap()

    async def _run() -> None:
        async with db_engine.session_scope() as s:
            accts = await repos.list_accounts(s, user_id=UUID(user_id) if user_id else None)
        t = Table("id", "user_id", "vendor", "label", "active")
        for a in accts:
            t.add_row(str(a.id), str(a.user_id), a.vendor, a.account_label, str(a.is_active))
        console.print(t)
        await db_engine.dispose()

    _async(_run())


@account_app.command("remove")
def admin_account_remove(account_id: str) -> None:
    _bootstrap()

    async def _run() -> None:
        async with db_engine.session_scope() as s:
            await repos.remove_account(s, UUID(account_id))
        console.print("[green]removed[/]")
        await db_engine.dispose()

    _async(_run())


# ----- admin: credentials -----


def _read_value(value: str | None) -> str:
    if value is None or value == "-":
        data = sys.stdin.read().strip()
        if not data:
            raise typer.BadParameter("empty value from stdin")
        return data
    return value


def _ref_for(account_id: UUID, key_name: str, vendor: str) -> str:
    return f"{vendor}/{account_id}/{key_name}"


async def _vendor_for(account_id: UUID) -> str:
    async with db_engine.session_scope() as s:
        accts = await repos.list_accounts(s)
    for a in accts:
        if a.id == account_id:
            return a.vendor
    raise typer.BadParameter("account not found")


@cred_app.command("set")
def admin_cred_set(
    account_id: str = typer.Option(..., "--account-id"),
    key: str = typer.Option(..., "--key"),
    value: str = typer.Option(None, "--value", help="literal or '-' for stdin"),
) -> None:
    _bootstrap()
    plaintext = _read_value(value)
    settings = get_settings()
    backend = build_backend(settings.vault)

    async def _run() -> None:
        aid = UUID(account_id)
        vendor = await _vendor_for(aid)
        ref = _ref_for(aid, key, vendor)
        await backend.put(ref, plaintext)
        async with db_engine.session_scope() as s:
            await repos.upsert_credential(s, account_id=aid, key_name=key, secret_ref=ref)
        console.print(f"[green]credential stored[/] ref={ref} (value redacted)")
        await db_engine.dispose()

    _async(_run())


@cred_app.command("rotate")
def admin_cred_rotate(
    account_id: str = typer.Option(..., "--account-id"),
    key: str = typer.Option(..., "--key"),
    value: str = typer.Option(None, "--value"),
) -> None:
    _bootstrap()
    plaintext = _read_value(value)
    settings = get_settings()
    backend = build_backend(settings.vault)

    async def _run() -> None:
        aid = UUID(account_id)
        async with db_engine.session_scope() as s:
            cred = await repos.get_credential(s, account_id=aid, key_name=key)
        if cred is None:
            console.print("[red]credential not found[/]")
            raise typer.Exit(1)
        await backend.rotate(cred.secret_ref, plaintext)
        async with db_engine.session_scope() as s:
            await repos.upsert_credential(
                s, account_id=aid, key_name=key, secret_ref=cred.secret_ref, rotated=True
            )
        console.print(f"[green]credential rotated[/] ref={cred.secret_ref}")
        await db_engine.dispose()

    _async(_run())


@cred_app.command("delete")
def admin_cred_delete(
    account_id: str = typer.Option(..., "--account-id"),
    key: str = typer.Option(..., "--key"),
) -> None:
    _bootstrap()
    settings = get_settings()
    backend = build_backend(settings.vault)

    async def _run() -> None:
        aid = UUID(account_id)
        async with db_engine.session_scope() as s:
            cred = await repos.get_credential(s, account_id=aid, key_name=key)
        if cred is None:
            console.print("[yellow]no such credential[/]")
            return
        try:
            await backend.delete(cred.secret_ref)
        except Exception as e:  # noqa: BLE001
            console.print(f"[yellow]vault delete failed[/]: {e}")
        async with db_engine.session_scope() as s:
            await repos.delete_credential(s, account_id=aid, key_name=key)
        console.print("[green]deleted[/]")
        await db_engine.dispose()

    _async(_run())


if __name__ == "__main__":
    app()
