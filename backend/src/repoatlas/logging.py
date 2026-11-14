"""Structured logging: readable console output locally, JSON lines in CI and deployment.

Every event and traceback passes through ``repoatlas.redact`` before it is written, so a
secret that ends up in a message (an exception text, a URL) is not logged.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from typing import Any, cast

import structlog
from structlog.typing import FilteringBoundLogger, Processor

from repoatlas.config import LogFormat, Settings
from repoatlas.redact import redact, redact_value, register_secrets


def configure(settings: Settings) -> None:
    """Configure logging from settings, and register the settings' secrets for redaction."""
    register_secrets(settings)
    configure_logging(settings.log_level, settings.log_format)


def configure_logging(level: str = "INFO", fmt: LogFormat = LogFormat.CONSOLE) -> None:
    """Configure structlog for RepoAtlas and the standard library for third-party libraries."""
    numeric_level = logging.getLevelNamesMapping()[level.upper()]
    renderer: Processor
    if fmt is LogFormat.JSON:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=_colors_supported())

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            _redact_event,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )
    # SQLAlchemy and Alembic log through the standard library; keep them at the same level.
    logging.basicConfig(
        level=numeric_level, stream=sys.stderr, format="%(levelname)s %(name)s: %(message)s"
    )
    root = logging.getLogger()
    root.setLevel(numeric_level)
    for handler in root.handlers:
        if not any(isinstance(item, _RedactFilter) for item in handler.filters):
            handler.addFilter(_RedactFilter())


def _redact_event(_logger: Any, _method: str, event: Any) -> Any:
    """The structlog processor that removes secrets from every value of an event."""
    return redact_value(event)


class _RedactFilter(logging.Filter):
    """Removes secrets from standard-library log records (third-party libraries)."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, None
        return True


def get_logger(name: str | None = None, **initial_values: Any) -> FilteringBoundLogger:
    """Return a structlog logger, optionally bound to a ``logger`` name and extra context."""
    logger = structlog.get_logger(**initial_values)
    if name is not None:
        # Bind after creation: structlog's own factory already has a parameter named "logger".
        logger = logger.bind(logger=name)
    return cast(FilteringBoundLogger, logger)


def _colors_supported() -> bool:
    if not sys.stderr.isatty():
        return False
    # structlog needs colorama for colors on Windows (it is installed there as a click dependency).
    return sys.platform != "win32" or importlib.util.find_spec("colorama") is not None
