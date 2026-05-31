"""structlog configuration with file rotation + secret redaction. See docs/LLD.md §8."""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from typing import Any

import structlog

from chronosync.config import LoggingSettings

_REDACT_KEYS = re.compile(r"(api[_-]?key|password|token|secret|access[_-]?token)", re.I)
_REDACT_VALUE = "***REDACTED***"


def _redact_secrets(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    for k in list(event_dict.keys()):
        if _REDACT_KEYS.search(k):
            event_dict[k] = _REDACT_VALUE
    return event_dict


def configure_logging(settings: LoggingSettings) -> None:
    level = getattr(logging, settings.level.upper(), logging.INFO)

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if settings.file is not None:
        settings.file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            logging.handlers.RotatingFileHandler(
                settings.file,
                maxBytes=settings.max_bytes,
                backupCount=settings.backups,
                encoding="utf-8",
            )
        )

    logging.basicConfig(
        format="%(message)s",
        level=level,
        handlers=handlers,
        force=True,
    )

    renderer = (
        structlog.processors.JSONRenderer()
        if settings.json_format
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_secrets,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name) if name else structlog.get_logger()
