"""FastAPI dependencies."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from chronosync.db import engine as db_engine


async def get_session() -> AsyncIterator[AsyncSession]:
    factory = db_engine.get_session_factory()
    async with factory() as s:
        yield s


def encode_cursor(payload: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    if not cursor:
        return None
    try:
        return json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid cursor: {e}") from e
