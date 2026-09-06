"""F&O refresh: NSE list parsing, empty-guard, and reconcile orchestration.

Network + DB are faked so this stays a true unit test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date

import pytest
from tenacity import AsyncRetrying, stop_after_attempt, wait_none

from chronosync.exceptions import ProviderError
from chronosync.sync import fno_refresh


class _FakeResp:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class _FakeClient:
    """Minimal httpx.AsyncClient stand-in: warm-up URLs return junk, the API
    URL returns the supplied payload."""

    def __init__(self, api_payload: object) -> None:
        self._api = api_payload
        self.gets: list[str] = []

    async def get(self, url: str):  # type: ignore[no-untyped-def]
        self.gets.append(url)
        if "underlying-information" in url:
            return _FakeResp(self._api)
        return _FakeResp({})

    async def aclose(self) -> None:
        return None


def test_parse_excludes_indices_and_uppercases() -> None:
    payload = {
        "data": {
            "UnderlyingList": [
                {"symbol": "reliance"},
                {"symbol": "TCS"},
                {"symbol": ""},
            ],
            "IndexList": [{"symbol": "NIFTY"}, {"symbol": "BANKNIFTY"}],
        }
    }
    assert fno_refresh._parse_underlying_symbols(payload) == ["RELIANCE", "TCS"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_returns_symbols() -> None:
    client = _FakeClient({"data": {"UnderlyingList": [{"symbol": "SBIN"}, {"symbol": "INFY"}]}})
    syms = await fno_refresh.fetch_nse_fno_symbols(client=client)  # type: ignore[arg-type]
    assert syms == ["SBIN", "INFY"]
    # Warm-up pages were hit before the API.
    assert any("underlying-information" in u for u in client.gets)
    assert len(client.gets) >= 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_raises_on_empty_list() -> None:
    client = _FakeClient({"data": {"UnderlyingList": []}})
    with pytest.raises(ProviderError):
        await fno_refresh.fetch_nse_fno_symbols(client=client)  # type: ignore[arg-type]


@pytest.fixture
def patched_persist(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Fake session_scope + set_fno_flags; record the reconcile call."""
    calls: list[dict] = []

    async def fake_set(_s, *, exchange, fno_tickers, as_of):  # type: ignore[no-untyped-def]
        calls.append({"exchange": exchange, "tickers": list(fno_tickers), "as_of": as_of})
        return (len(fno_tickers), 7)

    @asynccontextmanager
    async def fake_scope() -> AsyncIterator[object]:
        yield object()

    monkeypatch.setattr(fno_refresh.db_engine, "session_scope", fake_scope)
    monkeypatch.setattr(fno_refresh.repos, "set_fno_flags", fake_set)
    return calls


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_reconciles_with_fetched_symbols(monkeypatch, patched_persist) -> None:
    async def fake_fetch() -> list[str]:
        return ["SBIN", "INFY", "TCS"]

    monkeypatch.setattr(fno_refresh, "fetch_nse_fno_symbols", fake_fetch)
    summary = await fno_refresh.run(exchange="NSE", today=date(2026, 8, 1))

    assert summary.fetched == 3
    assert summary.marked == 3
    assert summary.cleared == 7
    assert len(patched_persist) == 1
    assert patched_persist[0] == {
        "exchange": "NSE",
        "tickers": ["SBIN", "INFY", "TCS"],
        "as_of": date(2026, 8, 1),
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_leaves_flags_untouched_on_persistent_failure(
    monkeypatch, patched_persist
) -> None:
    # No-wait retry so the give-up path doesn't sleep through the backoff.
    def fast_retrying(**kw):  # type: ignore[no-untyped-def]
        kw["wait"] = wait_none()
        kw["stop"] = stop_after_attempt(2)
        return AsyncRetrying(**kw)

    monkeypatch.setattr(fno_refresh, "AsyncRetrying", fast_retrying)

    async def boom() -> list[str]:
        raise ProviderError("nse down")

    monkeypatch.setattr(fno_refresh, "fetch_nse_fno_symbols", boom)
    summary = await fno_refresh.run(exchange="NSE", today=date(2026, 8, 1))

    assert summary.fetched == 0
    assert summary.marked == 0 and summary.cleared == 0
    # Crucially, the reconcile write was never attempted.
    assert patched_persist == []
