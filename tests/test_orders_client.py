"""
Tests for the Orders notification client.

All outbound HTTP calls are mocked with ``pytest-httpx``.
"""

import pytest
from pytest_httpx import HTTPXMock

from src.services.orders_client import notify_payment_result


class TestNotifyPaymentResult:
    """Tests for ``notify_payment_result``."""

    @pytest.mark.asyncio
    async def test_successful_notification(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """A 200 response from Orders returns True."""
        httpx_mock.add_response(
            url="http://orders-service:8000/orders/order-123/payment-webhook",
            method="POST",
            json={"status": "ok"},
            status_code=200,
        )

        result = await notify_payment_result("order-123", "APPROVED", "trans-123")

        assert result is True

        # Verify correct payload was sent.
        request = httpx_mock.get_request()
        body = __import__("json").loads(request.content)
        assert body == {"status": "SUCCESS", "transaction_id": "trans-123"}

    @pytest.mark.asyncio
    async def test_retries_on_transient_error(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """A 503 followed by a 200 should succeed after retry."""
        # First attempt fails, second succeeds.
        httpx_mock.add_response(
            url="http://orders-service:8000/orders/order-456/payment-webhook",
            method="POST",
            status_code=503,
        )
        httpx_mock.add_response(
            url="http://orders-service:8000/orders/order-456/payment-webhook",
            method="POST",
            json={"status": "ok"},
            status_code=200,
        )

        result = await notify_payment_result("order-456", "REJECTED", "trans-456")

        assert result is True
        # Should have made 2 requests.
        assert len(httpx_mock.get_requests()) == 2

    @pytest.mark.asyncio
    async def test_returns_false_after_max_retries(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """After 3 failures, returns False without raising."""
        for _ in range(3):
            httpx_mock.add_response(
                url="http://orders-service:8000/orders/order-789/payment-webhook",
                method="POST",
                status_code=500,
            )

        result = await notify_payment_result("order-789", "APPROVED", "trans-789")

        assert result is False
        assert len(httpx_mock.get_requests()) == 3
