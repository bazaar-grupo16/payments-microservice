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

from src.core.config import get_settings


_HEALTH_PROBE_PATHS = {"/livez", "/readyz"}


class HealthProbeAccessLogFilter(logging.Filter):
    """Suppress access logs for the health probe endpoints."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "uvicorn.access":
            return True

        if not isinstance(record.args, tuple) or len(record.args) < 3:
            return True

        return record.args[2] not in _HEALTH_PROBE_PATHS


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

    formatter = logging.Formatter(
        fmt=(
            '{"time":"%(asctime)s",'
            '"level":"%(levelname)s",'
            '"logger":"%(name)s",'
            '"message":"%(message)s"}'
        ),
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    # Avoid duplicate handlers on repeated calls.
    root.handlers.clear()
    root.addHandler(handler)

    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, HealthProbeAccessLogFilter) for item in access_logger.filters):
        access_logger.addFilter(HealthProbeAccessLogFilter())
