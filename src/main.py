"""
Bazaar Microservice — Application entry-point.

Creates the FastAPI application, registers routers, and configures
structured logging.  Database tables are created on startup via the
ORM metadata (suitable for development; use Alembic migrations in
production).
"""

import logging
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI

from src.api.health import router as health_router
from src.core.config import get_settings
from src.core.database import Base, engine, wait_for_db
from src.core.logging import setup_logging


settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application startup / shutdown lifecycle hook."""
    setup_logging()
    logger.info("Starting %s", settings.APP_NAME)
    # Wait for the database before creating tables.
    wait_for_db()
    # Create tables (dev convenience — use Alembic in production).
    Base.metadata.create_all(bind=engine)
    yield
    logger.info("Shutting down %s", settings.APP_NAME)


app = FastAPI(
    title=settings.APP_NAME,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── Routers ──────────────────────────────────────────
app.include_router(health_router)
