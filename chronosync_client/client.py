"""Thin async client over the ChronoSync read API. Auto-paginates."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import httpx

from chronosync_client.models import DailyBarDTO, HealthDTO, InstrumentDTO, Page, SyncStatusDTO


class ChronoSyncClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8088",
        *,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._owns = client is None
        self._client = client or httpx.AsyncClient(base_url=self._base_url, timeout=timeout)

    async def close(self) -> None:
        if self._owns:
            await self._client.aclose()

    async def __aenter__(self) -> ChronoSyncClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    # ----- health -----

    async def health(self) -> HealthDTO:
        r = await self._client.get("/healthz")
        r.raise_for_status()
        return HealthDTO.model_validate(r.json())

    # ----- instruments -----

    async def get_instrument(self, id_or_ticker: str) -> InstrumentDTO:
        r = await self._client.get(f"/instruments/{id_or_ticker}")
        r.raise_for_status()
        return InstrumentDTO.model_validate(r.json())

    async def list_instruments(
        self,
        *,
        exchange: str | None = None,
        active: bool | None = None,
        q: str | None = None,
        limit: int = 100,
    ) -> AsyncIterator[InstrumentDTO]:
        params: dict[str, Any] = {"limit": limit}
        if exchange:
            params["exchange"] = exchange
        if active is not None:
            params["active"] = str(active).lower()
        if q:
            params["q"] = q
        async for item in self._paginate("/instruments", params, InstrumentDTO):
            yield item

    # ----- bars -----

    async def daily_bars(
        self,
        instrument: str,
        frm: date,
        to: date,
        *,
        adjusted: bool = True,
        limit: int = 1000,
    ) -> AsyncIterator[DailyBarDTO]:
        params: dict[str, Any] = {
            "instrument": instrument,
            "from": frm.isoformat(),
            "to": to.isoformat(),
            "adjusted": str(adjusted).lower(),
            "limit": limit,
        }
        async for item in self._paginate("/bars/daily", params, DailyBarDTO):
            yield item

    # ----- sync -----

    async def sync_status(
        self,
        *,
        instrument: str | None = None,
        feed_name: str | None = None,
        limit: int = 100,
    ) -> AsyncIterator[SyncStatusDTO]:
        params: dict[str, Any] = {"limit": limit}
        if instrument:
            params["instrument"] = instrument
        if feed_name:
            params["feed_name"] = feed_name
        async for item in self._paginate("/sync/status", params, SyncStatusDTO):
            yield item

    # ----- internal -----

    async def _paginate(
        self,
        path: str,
        params: dict[str, Any],
        model: type[Any],
    ) -> AsyncIterator[Any]:
        cursor: str | None = None
        while True:
            q = dict(params)
            if cursor:
                q["cursor"] = cursor
            r = await self._client.get(path, params=q)
            r.raise_for_status()
            page = Page[model].model_validate(r.json())  # type: ignore[valid-type]
            for item in page.items:
                yield item
            if not page.next_cursor:
                return
            cursor = page.next_cursor
