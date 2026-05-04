"""
Tests for the health-check endpoints ``/livez`` and ``/readyz``.

These tests run against a real PostgreSQL instance — **no mocking**.
The database is provided by ``docker-compose.test.yml``.
"""

from fastapi.testclient import TestClient


class TestLivez:
    """``/livez`` must always return 200 without any external call."""

    def test_returns_200(self, client: TestClient) -> None:
        response = client.get("/livez")
        assert response.status_code == 200

    def test_body_contains_alive(self, client: TestClient) -> None:
        body = client.get("/livez").json()
        assert body == {"status": "alive"}


class TestReadyz:
    """``/readyz`` validates connectivity to the real PostgreSQL database."""

    def test_returns_200_when_db_is_up(self, client: TestClient) -> None:
        """With the docker-compose DB running, /readyz must return 200."""
        response = client.get("/readyz")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}
