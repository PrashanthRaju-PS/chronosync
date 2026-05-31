"""Health endpoint."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from chronosync.api.deps import get_session
from chronosync.api.schemas import HealthDTO
from chronosync.config import get_settings
from chronosync.db import repositories as repos

router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=HealthDTO)
async def healthz(session: AsyncSession = Depends(get_session)) -> HealthDTO:
    db_ok = True
    try:
        await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    last = await repos.last_global_sync_at(session)
    settings = get_settings()
    if last is not None:
        stale_at = datetime.now(timezone.utc) - timedelta(hours=settings.api.health_max_staleness_h)
        if last < stale_at:
            raise HTTPException(status_code=503, detail="sync stale")
    return HealthDTO(status="ok" if db_ok else "degraded", db_ok=db_ok, last_sync_at=last)
