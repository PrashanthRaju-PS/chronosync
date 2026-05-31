"""Instrument endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from chronosync.api.deps import decode_cursor, encode_cursor, get_session
from chronosync.api.schemas import InstrumentDTO, Page
from chronosync.db import repositories as repos

router = APIRouter(prefix="/instruments", tags=["instruments"])


@router.get("", response_model=Page[InstrumentDTO])
async def list_instruments(
    exchange: str | None = None,
    active: bool | None = None,
    q: str | None = None,
    cursor: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> Page[InstrumentDTO]:
    after = (decode_cursor(cursor) or {}).get("after_ticker")
    rows = await repos.find_instruments(
        session,
        exchange=exchange,
        active=active,
        q=q,
        after_ticker=after,
        limit=limit + 1,
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cur = encode_cursor({"after_ticker": page[-1].ticker}) if has_more else None
    return Page(items=[InstrumentDTO.model_validate(r) for r in page], next_cursor=next_cur)


@router.get("/{id_or_ticker}", response_model=InstrumentDTO)
async def get_one(
    id_or_ticker: str,
    session: AsyncSession = Depends(get_session),
) -> InstrumentDTO:
    inst = await repos.get_instrument(session, id_or_ticker)
    if inst is None:
        raise HTTPException(status_code=404, detail="instrument not found")
    return InstrumentDTO.model_validate(inst)
