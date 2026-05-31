"""Daily-bar query endpoints."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from chronosync.api.deps import decode_cursor, encode_cursor, get_session
from chronosync.api.schemas import DailyBarDTO, Page
from chronosync.db import repositories as repos

router = APIRouter(prefix="/bars", tags=["bars"])


@router.get("/daily", response_model=Page[DailyBarDTO])
async def daily_bars(
    instrument: str = Query(..., description="UUID or ticker"),
    frm: date = Query(..., alias="from"),
    to: date = Query(...),
    adjusted: bool = True,
    cursor: str | None = None,
    limit: int = Query(1000, ge=1, le=5000),
    session: AsyncSession = Depends(get_session),
) -> Page[DailyBarDTO]:
    inst = await repos.get_instrument(session, instrument)
    if inst is None:
        raise HTTPException(status_code=404, detail="instrument not found")
    after_ts = None
    if cursor:
        c = decode_cursor(cursor) or {}
        if "after_ts" in c:
            after_ts = date.fromisoformat(c["after_ts"])
    rows = await repos.daily_bars_range(
        session,
        instrument_id=inst.id,
        frm=frm,
        to=to,
        after_ts=after_ts,
        limit=limit + 1,
    )
    has_more = len(rows) > limit
    page = rows[:limit]
    items: list[DailyBarDTO] = []
    for r in page:
        dto = DailyBarDTO.model_validate(r)
        if adjusted:
            dto.adj_close = (dto.close * dto.adj_factor).quantize(dto.close)
        items.append(dto)
    next_cur = encode_cursor({"after_ts": page[-1].ts.isoformat()}) if has_more else None
    return Page(items=items, next_cursor=next_cur)
