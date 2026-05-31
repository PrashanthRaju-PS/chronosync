"""Seeder protocol."""

from __future__ import annotations

from typing import ClassVar, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession


@runtime_checkable
class BaseSeeder(Protocol):
    exchange: ClassVar[str]

    async def run(self, session: AsyncSession) -> int:
        """Fetch latest universe and upsert; return number of rows touched."""
        ...
