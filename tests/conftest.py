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
from sqlalchemy.orm import Session

from src.core.database import Base, SessionLocal, engine
from src.main import app


@pytest.fixture(scope="session", autouse=True)
def _setup_database():
    """Create all tables before the session and tear them down after."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client() -> TestClient:
    """Return a ``TestClient`` bound to the FastAPI application."""
    return TestClient(app)


@pytest.fixture()
def db_session() -> Session:
    """Yield a SQLAlchemy session and roll back after each test.

    This ensures every test starts with a clean database state
    without paying the cost of DROP/CREATE on every test.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        # Truncate all tables to keep tests isolated.
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()
        session.close()
