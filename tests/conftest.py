"""
Shared pytest fixtures for the test suite.

Tests run against a **real PostgreSQL** database provided by
``docker-compose.test.yml``.  Connection details come from the
same environment variables the application uses in production
(DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME).

**Database is NOT mocked.**
"""

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import Base, SessionLocal, engine
from src.main import app


import asyncio


@pytest.fixture(scope="session", autouse=True)
def event_loop():
    """Create a session-scoped event loop to avoid loop-closed and different-loop errors during DB teardown."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
async def _setup_database():
    """Create all tables before the session and tear them down after."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture()
def client() -> TestClient:
    """Return a ``TestClient`` bound to the FastAPI application."""
    return TestClient(app)


@pytest.fixture()
async def async_client() -> AsyncClient:
    """Return an ``AsyncClient`` bound to the FastAPI application."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


@pytest.fixture()
async def db_session() -> AsyncSession:
    """Yield a SQLAlchemy async session and roll back after each test.

    This ensures every test starts with a clean database state
    without paying the cost of DROP/CREATE on every test.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        await session.rollback()
        # Truncate all tables to keep tests isolated.
        for table in reversed(Base.metadata.sorted_tables):
            await session.execute(table.delete())
        await session.commit()
        await session.close()
        await engine.dispose()
