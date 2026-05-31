"""SecretsBackend protocol."""

from __future__ import annotations

from typing import ClassVar, Protocol, runtime_checkable


@runtime_checkable
class SecretsBackend(Protocol):
    backend_name: ClassVar[str]

    async def get(self, ref: str) -> str: ...
    async def put(self, ref: str, value: str) -> None: ...
    async def rotate(self, ref: str, new_value: str) -> None: ...
    async def delete(self, ref: str) -> None: ...
