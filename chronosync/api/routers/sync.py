"""Sync-status endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from chronosync.api.deps import get_session
from chronosync.api.schemas import Page, SyncStatusDTO
from chronosync.db import repositories as repos

router = APIRouter(prefix="/sync", tags=["sync"])


@router.get("/status", response_model=Page[SyncStatusDTO])
async def sync_status(
    instrument: str | None = None,
    feed_name: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> Page[SyncStatusDTO]:
    instrument_id = None
    if instrument:
        inst = await repos.get_instrument(session, instrument)
        if inst is None:
            raise HTTPException(status_code=404, detail="instrument not found")
        instrument_id = inst.id
    rows = await repos.sync_status_for(
        session,
        instrument_id=instrument_id,
        feed_name=feed_name,
        limit=limit,
    )
    return Page(items=[SyncStatusDTO.model_validate(r) for r in rows], next_cursor=None)
