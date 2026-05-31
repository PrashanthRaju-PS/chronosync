"""Provider registry — name → feed instance."""

from __future__ import annotations

from chronosync.config import ProviderSettings
from chronosync.exceptions import ConfigurationError
from chronosync.providers.base import BaseDataFeed
from chronosync.providers.yfinance_feed import YFinanceFeed


class ProviderRegistry:
    def __init__(self) -> None:
        self._feeds: dict[str, BaseDataFeed] = {}

    def register(self, feed: BaseDataFeed) -> None:
        self._feeds[feed.name] = feed

    def get(self, name: str) -> BaseDataFeed:
        if name not in self._feeds:
            raise ConfigurationError(f"unknown provider: {name!r}")
        return self._feeds[name]

    async def close_all(self) -> None:
        for feed in self._feeds.values():
            await feed.close()


_registry: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    if _registry is None:
        raise ConfigurationError("provider registry not bootstrapped")
    return _registry


def bootstrap_registry(settings: ProviderSettings) -> ProviderRegistry:
    global _registry
    reg = ProviderRegistry()
    reg.register(
        YFinanceFeed(
            concurrency=settings.yfinance_concurrency,
            retry_max_attempts=settings.retry_max_attempts,
            retry_base_delay_s=settings.retry_base_delay_s,
            retry_max_delay_s=settings.retry_max_delay_s,
        )
    )
    _registry = reg
    return reg
