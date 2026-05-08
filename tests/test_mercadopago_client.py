"""
Tests for the MercadoPago HTTP client.

All outbound HTTP calls are mocked with ``pytest-httpx``.
"""

import httpx
import pytest
from pytest_httpx import HTTPXMock

from src.services.mercadopago_client import (
    PreferenceResult,
    create_preference,
    get_payment_info,
)

_SAMPLE_ITEMS = [
    {"title": "Compra Bazaar", "quantity": 1, "unit_price": 100.0},
]


class TestCreatePreference:
    """Tests for ``create_preference``."""

    @pytest.mark.asyncio
    async def test_returns_preference_result(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """A successful MP response returns preference_id + init_point."""
        httpx_mock.add_response(
            url="https://api.mercadopago.com/checkout/preferences",
            method="POST",
            json={
                "id": "PREF-ABC-123",
                "init_point": "https://www.mercadopago.com/checkout/v1/redirect?pref_id=PREF-ABC-123",
            },
            status_code=201,
        )

        result = await create_preference(
            items=_SAMPLE_ITEMS,
            external_reference="order-uuid-1",
        )

        assert isinstance(result, PreferenceResult)
        assert result.preference_id == "PREF-ABC-123"
        assert "mercadopago" in result.init_point

    @pytest.mark.asyncio
    async def test_sends_correct_payload(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """The request body must contain the items list as provided."""
        httpx_mock.add_response(
            url="https://api.mercadopago.com/checkout/preferences",
            method="POST",
            json={"id": "PREF-X", "init_point": "https://mp.com/x"},
            status_code=201,
        )

        items = [
            {"title": "Widget", "quantity": 3, "unit_price": 49.99},
            {"title": "Gadget", "quantity": 1, "unit_price": 120.00},
        ]
        await create_preference(
            items=items,
            external_reference="ref-42",
        )

        request = httpx_mock.get_request()
        body = __import__("json").loads(request.content)
        assert len(body["items"]) == 2
        assert body["items"][0]["title"] == "Widget"
        assert body["items"][0]["quantity"] == 3
        assert body["items"][0]["unit_price"] == 49.99
        assert body["items"][1]["title"] == "Gadget"
        assert body["external_reference"] == "ref-42"

    @pytest.mark.asyncio
    async def test_sends_notification_url_and_back_urls(
        self, httpx_mock: HTTPXMock
    ) -> None:
        """notification_url and back_urls should be included in the payload."""
        httpx_mock.add_response(
            url="https://api.mercadopago.com/checkout/preferences",
            method="POST",
            json={"id": "PREF-Y", "init_point": "https://mp.com/y"},
            status_code=201,
        )

        notification_url = "https://payments.example.com/payments/mp-webhook"
        back_urls = {
            "success": "bazaar://order-polling/order-123",
            "pending": "bazaar://order-polling/order-123",
            "failure": "bazaar://order-polling/order-123",
        }

        await create_preference(
            items=_SAMPLE_ITEMS,
            external_reference="order-123",
            notification_url=notification_url,
            back_urls=back_urls,
        )

        request = httpx_mock.get_request()
        body = __import__("json").loads(request.content)
        assert body["notification_url"] == notification_url
        assert body["back_urls"] == back_urls

    @pytest.mark.asyncio
    async def test_raises_on_mp_error(self, httpx_mock: HTTPXMock) -> None:
        """A non-2xx response from MP should raise HTTPStatusError."""
        httpx_mock.add_response(
            url="https://api.mercadopago.com/checkout/preferences",
            method="POST",
            json={"message": "invalid_token"},
            status_code=401,
        )

        with pytest.raises(httpx.HTTPStatusError):
            await create_preference(
                items=_SAMPLE_ITEMS,
                external_reference="ref-err",
            )


class TestGetPaymentInfo:
    """Tests for ``get_payment_info``."""

    @pytest.mark.asyncio
    async def test_returns_payment_data(self, httpx_mock: HTTPXMock) -> None:
        """A successful response returns the full payment dict."""
        httpx_mock.add_response(
            url="https://api.mercadopago.com/v1/payments/12345",
            method="GET",
            json={
                "id": 12345,
                "status": "approved",
                "external_reference": "order-uuid-1",
            },
        )

        data = await get_payment_info("12345")

        assert data["id"] == 12345
        assert data["status"] == "approved"

    @pytest.mark.asyncio
    async def test_raises_on_not_found(self, httpx_mock: HTTPXMock) -> None:
        """A 404 from MP should raise HTTPStatusError."""
        httpx_mock.add_response(
            url="https://api.mercadopago.com/v1/payments/99999",
            method="GET",
            json={"message": "not_found"},
            status_code=404,
        )

        with pytest.raises(httpx.HTTPStatusError):
            await get_payment_info("99999")
