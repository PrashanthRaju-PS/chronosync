"""FastAPI read endpoints return shapes that match chronosync_client.models."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest

from chronosync.api.app import create_app
from chronosync.db import repositories as repos
from chronosync_client.models import DailyBarDTO, InstrumentDTO


@pytest.mark.functional
@pytest.mark.requires_docker
@pytest.mark.asyncio
async def test_instruments_and_bars_contracts(db_session) -> None:
    # Seed minimal state.
    await repos.upsert_instruments(
        db_session,
        [
            {
                "ticker": "APITEST",
                "exchange": "NSE",
                "asset_class": "EQUITY",
                "country_code": "IN",
                "currency": "INR",
                "isin": None,
                "is_active": True,
            }
        ],
    )
    inst = await repos.get_instrument(db_session, "APITEST")
    assert inst is not None
    await repos.upsert_daily_bars(
        db_session,
        instrument_id=inst.id,
        feed_name="yfinance",
        rows=[
            {
                "ts": date(2024, 1, 2),
                "open": Decimal("100"),
                "high": Decimal("105"),
                "low": Decimal("99"),
                "close": Decimal("104"),
                "volume": 1000,
                "vwap": None,
                "adj_factor": Decimal("1"),
                "open_interest": None,
            }
        ],
    )
    await db_session.commit()

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # /healthz
        r = await client.get("/healthz")
        assert r.status_code == 200

        # /instruments
        r = await client.get("/instruments")
        assert r.status_code == 200
        page = r.json()
        items = [InstrumentDTO.model_validate(it) for it in page["items"]]
        assert any(i.ticker == "APITEST" for i in items)

        # /instruments/{ticker}
        r = await client.get("/instruments/APITEST")
        assert r.status_code == 200
        InstrumentDTO.model_validate(r.json())

        # /bars/daily
        r = await client.get(
            "/bars/daily",
            params={"instrument": "APITEST", "from": "2024-01-01", "to": "2024-01-10"},
        )
        assert r.status_code == 200
        bars = r.json()["items"]
        assert len(bars) == 1
        dto = DailyBarDTO.model_validate(bars[0])
        assert dto.close == Decimal("104.000000")
        assert dto.adj_close is not None
