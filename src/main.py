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
from src.api.routers.payment_router import router as payment_router
from src.core.config import get_settings
from src.core.database import Base, engine, wait_for_db
from src.core.logging import setup_logging
from src.models.outbox_event import OutboxEvent  # noqa: F401
from src.core.observability import CorrelationIdMiddleware
from src.core.telemetry import initialize_prometheus_metrics, router as metrics_router


settings = get_settings()
logger = logging.getLogger(__name__)

initialize_prometheus_metrics()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application startup / shutdown lifecycle hook."""
    setup_logging()
    logger.info("Starting %s", settings.APP_NAME)
    # Wait for the database before creating tables.
    await wait_for_db()
    # Create tables (dev convenience — use Alembic in production).
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    worker_task = None
    if settings.OUTBOX_WORKER_ENABLED:
        import asyncio
        from src.services.outbox_worker import run_outbox_worker
        worker_task = asyncio.create_task(run_outbox_worker())
        logger.info("Started background outbox worker async task")

    yield

    if worker_task:
        import asyncio
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            logger.info("Background outbox worker task cancelled")
    logger.info("Shutting down %s", settings.APP_NAME)


app = FastAPI(
    title=settings.APP_NAME,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)
app.add_middleware(CorrelationIdMiddleware)

# ── Routers ──────────────────────────────────────────
app.include_router(health_router)
app.include_router(payment_router)
app.include_router(metrics_router)

