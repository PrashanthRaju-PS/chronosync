"""When Bhavcopy drops a ticker, deactivate_missing must flip is_active=false.

Verifies FR-1.2 end-to-end against a real DB. Bypasses the HTTP fetch by
exercising the parser→upsert→deactivate path directly with synthetic rows.
"""

from __future__ import annotations

import pytest

from chronosync.db import repositories as repos


@pytest.mark.functional
@pytest.mark.requires_docker
@pytest.mark.asyncio
async def test_dropped_ticker_is_deactivated(db_session) -> None:
    # Day 1 universe.
    day1 = [
        {
            "ticker": t,
            "exchange": "NSE",
            "asset_class": "EQUITY",
            "country_code": "IN",
            "currency": "INR",
            "isin": f"INE{t}XX",
            "is_active": True,
        }
        for t in ("DEACT_KEEP1", "DEACT_KEEP2", "DEACT_GONE")
    ]
    await repos.upsert_instruments(db_session, day1)
    await db_session.commit()

    # Confirm all three live initially.
    for t in ("DEACT_KEEP1", "DEACT_KEEP2", "DEACT_GONE"):
        inst = await repos.get_instrument(db_session, t)
        assert inst is not None and inst.is_active is True

    # Day 2 universe: DEACT_GONE is missing from the new Bhavcopy.
    day2_tickers = ["DEACT_KEEP1", "DEACT_KEEP2"]
    n_deactivated = await repos.deactivate_missing(
        db_session, exchange="NSE", seen_tickers=day2_tickers
    )
    await db_session.commit()
    assert n_deactivated >= 1  # at minimum DEACT_GONE

    db_session.expire_all()
    keep1 = await repos.get_instrument(db_session, "DEACT_KEEP1")
    keep2 = await repos.get_instrument(db_session, "DEACT_KEEP2")
    gone = await repos.get_instrument(db_session, "DEACT_GONE")
    assert keep1 is not None and keep1.is_active is True
    assert keep2 is not None and keep2.is_active is True
    assert gone is not None and gone.is_active is False


@pytest.mark.functional
@pytest.mark.requires_docker
@pytest.mark.asyncio
async def test_relisting_via_upsert_reactivates(db_session) -> None:
    # Pre-seed an inactive instrument (simulating a previously-deactivated ticker).
    await repos.upsert_instruments(
        db_session,
        [
            {
                "ticker": "REACT_X",
                "exchange": "NSE",
                "asset_class": "EQUITY",
                "country_code": "IN",
                "currency": "INR",
                "isin": None,
                "is_active": False,
            }
        ],
    )
    await db_session.commit()

    # Bhavcopy lists it again — upsert with is_active=true should reactivate it.
    await repos.upsert_instruments(
        db_session,
        [
            {
                "ticker": "REACT_X",
                "exchange": "NSE",
                "asset_class": "EQUITY",
                "country_code": "IN",
                "currency": "INR",
                "isin": "INE_NEW",
                "is_active": True,
            }
        ],
    )
    await db_session.commit()

    db_session.expire_all()
    inst = await repos.get_instrument(db_session, "REACT_X")
    assert inst is not None
    assert inst.is_active is True
    assert inst.isin == "INE_NEW"
