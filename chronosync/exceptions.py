"""Error taxonomy. See docs/LLD.md §7."""

from __future__ import annotations


class ChronoSyncError(Exception):
    """Root exception."""


class ConfigurationError(ChronoSyncError):
    pass


class DatabaseError(ChronoSyncError):
    pass


class DatabaseUnavailable(DatabaseError):
    pass


class UpsertConflict(DatabaseError):
    pass


class ProviderError(ChronoSyncError):
    pass


class RetriableProviderError(ProviderError):
    """Transient: 429, 5xx, network. Tenacity retries these."""


class PermanentProviderError(ProviderError):
    """Terminal: bad symbol, 404, malformed response. Not retried."""


class SeederError(ChronoSyncError):
    pass


class BhavcopyFetchError(SeederError):
    pass


class SecretsError(ChronoSyncError):
    pass


class VaultUnlocked(SecretsError):
    pass


class SecretNotFound(SecretsError):
    pass


class PlannerError(ChronoSyncError):
    pass
