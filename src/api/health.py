"""
Health-check endpoints.

**/livez** and **/readyz** have **completely separate logic** as required
by the observability specification:

- ``/livez``  → verifies the HTTP process is alive (no external calls).
- ``/readyz`` → verifies critical dependencies (database) are reachable.
"""

import logging

from fastapi import APIRouter, Response, status

from src.core.database import check_db_connection

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])


@router.get(
    "/livez",
    status_code=status.HTTP_200_OK,
    summary="Liveness probe",
    description="Returns HTTP 200 when the process is alive. No external checks.",
)
async def livez() -> dict[str, str]:
    """Liveness probe — always returns 200 if the server is up."""
    return {"status": "alive"}


@router.get(
    "/readyz",
    summary="Readiness probe",
    description=(
        "Attempts a lightweight database query. "
        "Returns HTTP 200 if successful, HTTP 503 otherwise."
    ),
)
async def readyz(response: Response) -> dict[str, str]:
    """Readiness probe — checks database connectivity."""
    if await check_db_connection():
        return {"status": "ready"}

    logger.error("Readiness check failed: database unreachable")
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "unavailable"}
