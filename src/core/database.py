"""
SQLAlchemy engine, session factory, and declarative base.

The connection string is built from environment variables via
:pymod:`src.core.config` — no credentials appear in this file.
"""

import logging
import time

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from src.core.config import get_settings


settings = get_settings()
logger = logging.getLogger(__name__)

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


class Base(DeclarativeBase):
    """Declarative base class for all ORM models."""


def check_db_connection() -> bool:
    """
    Attempt a lightweight query against the database.

    Returns
    -------
    bool
        ``True`` if the database is reachable, ``False`` otherwise.
    """
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


# ── Retry / backoff knobs ─────────────────────────────────────
_DB_INITIAL_WAIT: float = 1.0    # seconds before the first retry
_DB_BACKOFF_FACTOR: float = 2.0  # multiplier applied after each failure
_DB_MAX_WAIT: float = 30.0       # ceiling for a single sleep interval
_DB_TIMEOUT: float = 300.0       # total budget — give up after this many seconds


def wait_for_db() -> None:
    """
    Block until the database is reachable or the retry budget is exhausted.

    Implements exponential backoff with a per-sleep cap (``_DB_MAX_WAIT``)
    and a hard total timeout (``_DB_TIMEOUT``).  Intended to be called once
    during application startup before attempting any schema operations.

    Sleep schedule (seconds): 1 → 2 → 4 → 8 → 16 → 30 → 30 → …

    Raises
    ------
    RuntimeError
        If the database is still unreachable after ``_DB_TIMEOUT`` seconds.
    """
    deadline = time.monotonic() + _DB_TIMEOUT
    wait = _DB_INITIAL_WAIT
    attempt = 0

    while True:
        attempt += 1
        if check_db_connection():
            logger.info("Database is reachable (attempt %d).", attempt)
            return

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(
                f"Database unreachable after {_DB_TIMEOUT:.0f}s "
                f"({attempt} attempts). Aborting startup."
            )

        sleep_for = min(wait, remaining, _DB_MAX_WAIT)
        logger.warning(
            "Database not ready (attempt %d). Retrying in %.1fs "
            "(%.0fs remaining in budget).",
            attempt,
            sleep_for,
            remaining,
        )
        time.sleep(sleep_for)
        wait = min(wait * _DB_BACKOFF_FACTOR, _DB_MAX_WAIT)
