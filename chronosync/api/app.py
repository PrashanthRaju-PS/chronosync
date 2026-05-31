"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from chronosync.api.routers import bars, health, instruments, sync
from chronosync.config import get_settings
from chronosync.db import engine as db_engine
from chronosync.logging import configure_logging


@asynccontextmanager
async def _lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
    settings = get_settings()
    configure_logging(settings.logging)
    db_engine.init_engine(settings.db)
    await db_engine.ping()
    try:
        yield
    finally:
        await db_engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="ChronoSync", version="0.1.0", lifespan=_lifespan)
    app.include_router(health.router)
    app.include_router(instruments.router)
    app.include_router(bars.router)
    app.include_router(sync.router)
    return app


app = create_app()
