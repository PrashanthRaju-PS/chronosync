"""set_fno_flags / fno_watermark against a real DB."""

from __future__ import annotations

from datetime import date

import pytest

from chronosync.db import repositories as repos


def _eq_rows(tickers):  # type: ignore[no-untyped-def]
    return [
        {
            "ticker": t,
            "exchange": "NSE",
            "asset_class": "EQUITY",
            "country_code": "IN",
            "currency": "INR",
            "isin": None,
            "is_active": True,
        }
        for t in tickers
    ]


@pytest.mark.functional
@pytest.mark.requires_docker
@pytest.mark.asyncio
async def test_set_fno_flags_marks_clears_and_stamps(db_session) -> None:
    await repos.upsert_instruments(db_session, _eq_rows(["FNOAAA", "FNOBBB", "FNOCCC"]))
    await db_session.commit()

    marked, cleared = await repos.set_fno_flags(
        db_session,
        exchange="NSE",
        fno_tickers=["FNOAAA", "fnobbb"],  # mixed case → matched case-insensitively
        as_of=date(2026, 8, 1),
    )
    await db_session.commit()
    # Two of ours marked; everything else on NSE cleared (>=1, our FNOCCC).
    assert marked == 2
    assert cleared >= 1

    db_session.expire_all()
    a = await repos.get_instrument(db_session, "FNOAAA")
    b = await repos.get_instrument(db_session, "FNOBBB")
    c = await repos.get_instrument(db_session, "FNOCCC")
    assert a is not None and a.is_fno is True and a.fno_as_of == date(2026, 8, 1)
    assert b is not None and b.is_fno is True
    assert c is not None and c.is_fno is False and c.fno_as_of == date(2026, 8, 1)

    wm = await repos.fno_watermark(db_session, exchange="NSE")
    assert wm == date(2026, 8, 1)


@pytest.mark.functional
@pytest.mark.requires_docker
@pytest.mark.asyncio
async def test_set_fno_flags_empty_is_noop(db_session) -> None:
    await repos.upsert_instruments(db_session, _eq_rows(["FNOKEEP"]))
    await db_session.commit()
    # Pre-mark it, then prove an empty fetch does NOT clear it.
    await repos.set_fno_flags(
        db_session, exchange="NSE", fno_tickers=["FNOKEEP"], as_of=date(2026, 7, 1)
    )
    await db_session.commit()

    marked, cleared = await repos.set_fno_flags(
        db_session, exchange="NSE", fno_tickers=[], as_of=date(2026, 8, 1)
    )
    assert (marked, cleared) == (0, 0)

    db_session.expire_all()
    keep = await repos.get_instrument(db_session, "FNOKEEP")
    assert keep is not None and keep.is_fno is True and keep.fno_as_of == date(2026, 7, 1)


@pytest.mark.functional
@pytest.mark.requires_docker
@pytest.mark.asyncio
async def test_find_instruments_fno_filter(db_session) -> None:
    await repos.upsert_instruments(db_session, _eq_rows(["FILTA", "FILTB"]))
    await db_session.commit()
    await repos.set_fno_flags(
        db_session, exchange="NSE", fno_tickers=["FILTA"], as_of=date(2026, 8, 1)
    )
    await db_session.commit()
    db_session.expire_all()

    fno = await repos.find_instruments(db_session, exchange="NSE", fno=True, limit=500)
    tickers = {i.ticker for i in fno}
    assert "FILTA" in tickers
    assert "FILTB" not in tickers
