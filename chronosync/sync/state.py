"""Convenience accessors for sync_state. Thin wrappers over repositories."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from chronosync.db import engine as db_engine
from chronosync.db import repositories as repos


async def last_global_sync_at() -> datetime | None:
    async with db_engine.session_scope() as s:
        return await repos.last_global_sync_at(s)


async def status(instrument_id: UUID | None = None, feed_name: str | None = None):  # type: ignore[no-untyped-def]
    async with db_engine.session_scope() as s:
        return await repos.sync_status_for(s, instrument_id=instrument_id, feed_name=feed_name)
