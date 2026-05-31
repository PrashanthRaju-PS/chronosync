"""HashiCorp Vault backend — stub. Wire to hvac when deploying."""

from __future__ import annotations

from typing import ClassVar


class HashicorpVaultBackend:
    backend_name: ClassVar[str] = "hashicorp"

    def __init__(self, *_: object, **__: object) -> None:
        raise NotImplementedError(
            "HashiCorp Vault backend not yet implemented. "
            "Wire to `hvac` and configure CHRONOSYNC_VAULT__* when deploying."
        )

    async def get(self, ref: str) -> str:
        raise NotImplementedError

    async def put(self, ref: str, value: str) -> None:
        raise NotImplementedError

    async def rotate(self, ref: str, new_value: str) -> None:
        raise NotImplementedError

    async def delete(self, ref: str) -> None:
        raise NotImplementedError
