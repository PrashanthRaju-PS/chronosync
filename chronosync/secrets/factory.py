"""Backend selection from VaultSettings."""

from __future__ import annotations

from chronosync.config import VaultSettings
from chronosync.exceptions import ConfigurationError
from chronosync.secrets.base import SecretsBackend
from chronosync.secrets.keyring_backend import KeyringBackend
from chronosync.secrets.local import LocalEncryptedBackend
from chronosync.secrets.vault import HashicorpVaultBackend


def build_backend(settings: VaultSettings) -> SecretsBackend:
    if settings.backend == "local":
        return LocalEncryptedBackend(path=settings.path, master_key_env=settings.master_key_env)
    if settings.backend == "keyring":
        return KeyringBackend()
    if settings.backend == "hashicorp":
        return HashicorpVaultBackend()
    raise ConfigurationError(f"unknown vault backend: {settings.backend!r}")
