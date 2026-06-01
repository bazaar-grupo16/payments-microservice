"""
Structured JSON logging configuration.

Log levels follow the red-lines specification:
  - **ERROR**: failure that prevents the feature from executing.
  - **WARN**: anomalous condition that does not prevent basic functionality.
  - **INFO**: user- or system-initiated action, traceable in history.
  - **DEBUG**: technical context for diagnosis without needing source code.

The level is configurable via the ``LOG_LEVEL`` environment variable.
"""

import logging
import sys
from typing import Optional

from opentelemetry import trace

from src.core.config import get_settings
from src.core.observability import ObservabilityJSONFormatter


_HEALTH_PROBE_PATHS = {"/livez", "/readyz"}


class HealthProbeAccessLogFilter(logging.Filter):
    """Suppress access logs for the health probe endpoints."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "uvicorn.access":
            return True

        if not isinstance(record.args, tuple) or len(record.args) < 3:
            return True

        return record.args[2] not in _HEALTH_PROBE_PATHS


class OtelTraceFilter(logging.Filter):
    """Inject OTel trace context into log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            record.trace_id = format(span_context.trace_id, "032x")
            record.span_id = format(span_context.span_id, "016x")
        else:
            record.trace_id = None
            record.span_id = None

        return True


def _configure_application_logger(level: int, handler: logging.Handler) -> None:
    """Attach structured logging to the application logger namespace."""
    app_logger = logging.getLogger("src")
    app_logger.setLevel(level)
    app_logger.handlers.clear()
    app_logger.propagate = False

    if not any(isinstance(item, OtelTraceFilter) for item in app_logger.filters):
        app_logger.addFilter(OtelTraceFilter())

    if not any(existing_handler is handler for existing_handler in app_logger.handlers):
        app_logger.addHandler(handler)


def setup_logging(level_override: Optional[str] = None) -> None:
    """
    Configure the root logger with a structured JSON-like format.

    Parameters
    ----------
    level_override:
        If provided, overrides the level from ``Settings.LOG_LEVEL``.
    """
    settings = get_settings()
    level_name = (level_override or settings.LOG_LEVEL).upper()
    level = getattr(logging, level_name, logging.INFO)

    formatter = ObservabilityJSONFormatter()

    root_handler = logging.StreamHandler(stream=sys.stdout)
    root_handler.setFormatter(formatter)

    app_handler = logging.StreamHandler(stream=sys.stdout)
    app_handler.setFormatter(formatter)
    app_handler.addFilter(OtelTraceFilter())

    root = logging.getLogger()
    root.setLevel(level)
    # Avoid duplicate handlers on repeated calls.
    root.handlers.clear()
    root.addHandler(root_handler)

    _configure_application_logger(level, app_handler)

    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, HealthProbeAccessLogFilter) for item in access_logger.filters):
        access_logger.addFilter(HealthProbeAccessLogFilter())
