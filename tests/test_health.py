"""
Tests for the health-check endpoints ``/livez`` and ``/readyz``.

These tests run against a real PostgreSQL instance — **no mocking**.
The database is provided by ``docker-compose.test.yml``.
"""

from httpx import AsyncClient


class TestLivez:
    """``/livez`` must always return 200 without any external call."""

    async def test_returns_200(self, async_client: AsyncClient) -> None:
        response = await async_client.get("/livez")
        assert response.status_code == 200

    async def test_body_contains_alive(self, async_client: AsyncClient) -> None:
        response = await async_client.get("/livez")
        body = response.json()
        assert body == {"status": "alive"}


class TestReadyz:
    """``/readyz`` validates connectivity to the real PostgreSQL database."""

    async def test_returns_200_when_db_is_up(self, async_client: AsyncClient) -> None:
        """With the docker-compose DB running, /readyz must return 200."""
        response = await async_client.get("/readyz")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}
