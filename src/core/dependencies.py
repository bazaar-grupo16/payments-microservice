"""
FastAPI dependencies for dependency injection.

Add shared dependencies here (e.g. database sessions, auth context).
"""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database import SessionLocal


async def _get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a SQLAlchemy async session and ensure it is closed afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        await db.close()


# Re-usable annotated dependency for route signatures.
DbSession = Annotated[AsyncSession, Depends(_get_db)]
