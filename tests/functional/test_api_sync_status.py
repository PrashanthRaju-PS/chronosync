"""GET /sync/status — FR-4.5.

Seeds an instrument + sync_state row, then asserts the endpoint returns the
expected SyncStatusDTO shape (incl. filter by instrument).
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from chronosync.api.app import create_app
from chronosync.db import repositories as repos
from chronosync_client.models import SyncStatusDTO


@pytest.mark.functional
@pytest.mark.requires_docker
@pytest.mark.asyncio
async def test_sync_status_endpoint(db_session) -> None:
    await repos.upsert_instruments(
        db_session,
        [
            {
                "ticker": "STATTEST",
                "exchange": "NSE",
                "asset_class": "EQUITY",
                "country_code": "IN",
                "currency": "INR",
                "isin": None,
                "is_active": True,
            }
        ],
    )
    inst = await repos.get_instrument(db_session, "STATTEST")
    assert inst is not None
    iid = inst.id

    await repos.mark_sync_success(
        db_session,
        instrument_id=iid,
        feed_name="yfinance",
        last_ts=date(2026, 5, 29),
    )
    await db_session.commit()

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Unfiltered — at least our row is present.
        r = await client.get("/sync/status")
        assert r.status_code == 200
        items = r.json()["items"]
        assert any(it["instrument_id"] == str(iid) for it in items)

        # Filter to our instrument by UUID.
        r = await client.get("/sync/status", params={"instrument": str(iid)})
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 1
        dto = SyncStatusDTO.model_validate(items[0])
        assert dto.feed_name == "yfinance"
        assert dto.last_synced_ts == date(2026, 5, 29)
        assert dto.last_error is None

        # Filter to our instrument by ticker (resolved server-side).
        r = await client.get("/sync/status", params={"instrument": "STATTEST"})
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 1

        # Unknown instrument -> 404.
        r = await client.get("/sync/status", params={"instrument": "DOES_NOT_EXIST"})
        assert r.status_code == 404
