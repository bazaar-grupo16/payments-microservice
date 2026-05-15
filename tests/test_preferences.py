"""
Integration tests for ``POST /payments/preferences``.

- MercadoPago HTTP calls are **mocked** via ``pytest-httpx``.
- The database is a **real PostgreSQL** instance.
"""

import json
import uuid

from fastapi.testclient import TestClient
from pytest_httpx import HTTPXMock
from sqlalchemy.orm import Session

from src.models.payment import Payment

_VALID_ITEMS = [
    {"title": "Producto A", "quantity": 2, "unit_price": 50.0, "line_item_id": "item-producto-a"},
    {"title": "Producto B", "quantity": 1, "unit_price": 100.0, "line_item_id": "item-producto-b"},
]


def _mp_success_response(pref_id: str = "PREF-TEST-1") -> dict:
    """Build a realistic MercadoPago preference response."""
    return {
        "id": pref_id,
        "init_point": f"https://www.mercadopago.com.ar/checkout/v1/redirect?pref_id={pref_id}",
    }


class TestCreatePreference:
    """Tests for ``POST /payments/preferences``."""

    def test_creates_preference_successfully(
        self,
        client: TestClient,
        httpx_mock: HTTPXMock,
        db_session: Session,
    ) -> None:
        """A valid request returns 201 with preference_id + init_point."""
        httpx_mock.add_response(
            url="https://api.mercadopago.com/checkout/preferences",
            method="POST",
            json=_mp_success_response(),
            status_code=201,
        )

        order_id = str(uuid.uuid4())
        response = client.post(
            "/payments/preferences",
            json={"order_id": order_id, "items": _VALID_ITEMS},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )

        assert response.status_code == 201
        body = response.json()
        assert body["preference_id"] == "PREF-TEST-1"
        assert "mercadopago" in body["init_point"]

        # Verify that notification_url and back_urls were sent.
        request = httpx_mock.get_request()
        request_body = json.loads(request.content)
        assert "notification_url" in request_body
        assert "back_urls" in request_body
        assert request_body["back_urls"]["success"] == f"bazaar://order-polling/{order_id}"
        assert request_body["back_urls"]["pending"] == f"bazaar://order-polling/{order_id}"
        assert request_body["back_urls"]["failure"] == f"bazaar://order-polling/{order_id}"

    def test_idempotency_returns_cached(
        self,
        client: TestClient,
        httpx_mock: HTTPXMock,
        db_session: Session,
    ) -> None:
        """Same Idempotency-Key returns the cached init_point
        WITHOUT calling MercadoPago again."""
        httpx_mock.add_response(
            url="https://api.mercadopago.com/checkout/preferences",
            method="POST",
            json=_mp_success_response("PREF-IDEMP"),
            status_code=201,
        )

        key = str(uuid.uuid4())
        order_id = str(uuid.uuid4())
        payload = {"order_id": order_id, "items": _VALID_ITEMS}
        headers = {"Idempotency-Key": key}

        # First call — hits MercadoPago.
        r1 = client.post(
            "/payments/preferences",
            json=payload,
            headers=headers,
        )
        assert r1.status_code == 201

        # Second call — same key, should NOT call MercadoPago.
        r2 = client.post(
            "/payments/preferences",
            json=payload,
            headers=headers,
        )
        assert r2.status_code == 201
        assert r2.json()["preference_id"] == "PREF-IDEMP"
        assert r2.json()["init_point"] == r1.json()["init_point"]

        # Only 1 request should have been made to MercadoPago.
        assert len(httpx_mock.get_requests()) == 1

    def test_missing_idempotency_key_returns_422(
        self,
        client: TestClient,
    ) -> None:
        """A request without the Idempotency-Key header returns 422."""
        response = client.post(
            "/payments/preferences",
            json={
                "order_id": str(uuid.uuid4()),
                "items": _VALID_ITEMS,
            },
            # No Idempotency-Key header.
        )
        assert response.status_code == 422

    def test_invalid_body_returns_422(
        self,
        client: TestClient,
    ) -> None:
        """A malformed request body returns 422."""
        response = client.post(
            "/payments/preferences",
            json={"invalid": "data"},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        assert response.status_code == 422

    def test_empty_items_returns_502(
        self,
        client: TestClient,
        httpx_mock: HTTPXMock,
        db_session: Session,
    ) -> None:
        """An empty items list passes schema validation but MP rejects it."""
        httpx_mock.add_response(
            url="https://api.mercadopago.com/checkout/preferences",
            method="POST",
            json={"message": "items required"},
            status_code=400,
        )

        response = client.post(
            "/payments/preferences",
            json={
                "order_id": str(uuid.uuid4()),
                "items": [],
            },
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )
        assert response.status_code == 502

    def test_mp_error_returns_502(
        self,
        client: TestClient,
        httpx_mock: HTTPXMock,
        db_session: Session,
    ) -> None:
        """A MercadoPago API failure returns 502 Bad Gateway."""
        httpx_mock.add_response(
            url="https://api.mercadopago.com/checkout/preferences",
            method="POST",
            json={"message": "internal_error"},
            status_code=500,
        )

        response = client.post(
            "/payments/preferences",
            json={
                "order_id": str(uuid.uuid4()),
                "items": _VALID_ITEMS,
            },
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )

        assert response.status_code == 502
        assert "MercadoPago" in response.json()["detail"]

    def test_payment_persisted_in_db(
        self,
        client: TestClient,
        httpx_mock: HTTPXMock,
        db_session: Session,
    ) -> None:
        """After a successful call the payment record exists in PostgreSQL."""
        httpx_mock.add_response(
            url="https://api.mercadopago.com/checkout/preferences",
            method="POST",
            json=_mp_success_response("PREF-DB-CHECK"),
            status_code=201,
        )

        key = str(uuid.uuid4())
        order_id = str(uuid.uuid4())
        client.post(
            "/payments/preferences",
            json={"order_id": order_id, "items": _VALID_ITEMS},
            headers={"Idempotency-Key": key},
        )

        # Verify persistence.
        payment = (
            db_session.query(Payment)
            .filter(Payment.idempotency_key == key)
            .first()
        )
        assert payment is not None
        assert str(payment.order_id) == order_id
        assert payment.mp_preference_id == "PREF-DB-CHECK"
        assert payment.init_point is not None

    def test_sends_correct_payload_to_mp(
        self,
        client: TestClient,
        httpx_mock: HTTPXMock,
        db_session: Session,
    ) -> None:
        """The items list is sent to MercadoPago exactly as provided."""
        httpx_mock.add_response(
            url="https://api.mercadopago.com/checkout/preferences",
            method="POST",
            json=_mp_success_response(),
            status_code=201,
        )

        order_id = str(uuid.uuid4())
        client.post(
            "/payments/preferences",
            json={"order_id": order_id, "items": _VALID_ITEMS},
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )

        mp_request = httpx_mock.get_request()
        mp_body = json.loads(mp_request.content)
        assert len(mp_body["items"]) == 2
        assert mp_body["items"][0]["title"] == "Producto A"
        assert mp_body["items"][1]["unit_price"] == 100.0
        assert mp_body["external_reference"] == order_id
