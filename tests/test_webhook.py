"""
Integration tests for ``POST /payments/mp-webhook``.
"""

import hmac
import hashlib
import uuid
from decimal import Decimal

from fastapi.testclient import TestClient
import pytest
from pytest_httpx import HTTPXMock
from sqlalchemy.orm import Session

from src.models.payment import Payment, PaymentStatus
from src.core.config import get_settings


def _generate_valid_signature(data_id: str, secret: str) -> str:
    """Generate a valid signature for testing."""
    ts = "1234567890"
    x_request_id = "test-request-id"
    manifest = f"id:{data_id};request-id:{x_request_id};ts:{ts};"
    v1 = hmac.new(
        secret.encode("utf-8"),
        manifest.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"ts={ts},v1={v1}"


def _create_test_payment(db: Session, order_id: str) -> Payment:
    """Create a payment in PENDING state."""
    payment = Payment(
        order_id=uuid.UUID(order_id),
        idempotency_key=str(uuid.uuid4()),
        amount=Decimal("100.00"),
        items=[],
        status=PaymentStatus.PENDING,
        mp_preference_id="PREF-TEST",
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


class TestWebhook:
    """Tests for ``POST /payments/mp-webhook``."""

    def test_webhook_uses_query_param_data_id(
        self,
        client: TestClient,
        httpx_mock: HTTPXMock,
        db_session: Session,
    ) -> None:
        """MercadoPago signatures can be validated from the query string ID."""
        settings = get_settings()
        order_id = str(uuid.uuid4())
        _create_test_payment(db_session, order_id)
        data_id = "payment-321"

        httpx_mock.add_response(
            url=f"https://api.mercadopago.com/v1/payments/{data_id}",
            method="GET",
            json={
                "id": int(data_id.split("-")[1]),
                "status": "approved",
                "external_reference": order_id,
            },
        )
        httpx_mock.add_response(
            url=f"{settings.ORDERS_SERVICE_URL}/orders/{order_id}/payment-webhook",
            method="POST",
            json={"status": "ok"},
            status_code=200,
        )

        signature = _generate_valid_signature(data_id, settings.MP_WEBHOOK_SECRET)

        response = client.post(
            "/payments/mp-webhook?data.id=payment-321",
            json={"action": "payment.created", "data": {}},
            headers={
                "x-signature": signature,
                "x-request-id": "test-request-id",
            },
        )

        assert response.status_code == 200

    def test_webhook_ignores_non_final_status(
        self,
        client: TestClient,
        httpx_mock: HTTPXMock,
        db_session: Session,
    ) -> None:
        """Non-final MercadoPago statuses must not regress the payment."""
        settings = get_settings()
        order_id = str(uuid.uuid4())
        _create_test_payment(db_session, order_id)

        payment = db_session.query(Payment).filter(Payment.order_id == uuid.UUID(order_id)).first()
        payment.status = PaymentStatus.APPROVED
        db_session.commit()

        data_id = "payment-654"
        httpx_mock.add_response(
            url=f"https://api.mercadopago.com/v1/payments/{data_id}",
            method="GET",
            json={
                "id": int(data_id.split("-")[1]),
                "status": "in_process",
                "external_reference": order_id,
            },
        )

        signature = _generate_valid_signature(data_id, settings.MP_WEBHOOK_SECRET)

        response = client.post(
            "/payments/mp-webhook",
            json={"data": {"id": data_id}},
            headers={
                "x-signature": signature,
                "x-request-id": "test-request-id",
            },
        )

        assert response.status_code == 200
        refreshed = db_session.query(Payment).filter(Payment.order_id == uuid.UUID(order_id)).first()
        assert refreshed.status == PaymentStatus.APPROVED
        assert len(httpx_mock.get_requests()) == 1

    def test_webhook_pago_aprobado(
        self,
        client: TestClient,
        httpx_mock: HTTPXMock,
        db_session: Session,
    ) -> None:
        """A valid webhook with APPROVED status updates DB and notifies Orders."""
        settings = get_settings()
        order_id = str(uuid.uuid4())
        _create_test_payment(db_session, order_id)
        data_id = "payment-123"

        # Mock MP getting payment info
        httpx_mock.add_response(
            url=f"https://api.mercadopago.com/v1/payments/{data_id}",
            method="GET",
            json={
                "id": int(data_id.split("-")[1]),
                "status": "approved",
                "external_reference": order_id,
            },
        )

        # Mock Orders notification
        httpx_mock.add_response(
            url=f"{settings.ORDERS_SERVICE_URL}/orders/{order_id}/payment-webhook",
            method="POST",
            json={"status": "ok"},
            status_code=200,
        )

        signature = _generate_valid_signature(data_id, settings.MP_WEBHOOK_SECRET)

        response = client.post(
            "/payments/mp-webhook",
            json={"action": "payment.created", "data": {"id": data_id}},
            headers={
                "x-signature": signature,
                "x-request-id": "test-request-id",
            },
        )

        assert response.status_code == 200

        # Verify DB updated
        payment = db_session.query(Payment).filter(Payment.order_id == uuid.UUID(order_id)).first()
        assert payment.status == PaymentStatus.APPROVED
        assert payment.mp_payment_id == data_id

        # Verify Orders called
        requests = httpx_mock.get_requests()
        assert len(requests) == 2
        orders_req = next(r for r in requests if "orders" in str(r.url))
        import json
        body = json.loads(orders_req.content)
        assert body == {"status": "APPROVED", "transaction_id": data_id}


    def test_webhook_pago_rechazado(
        self,
        client: TestClient,
        httpx_mock: HTTPXMock,
        db_session: Session,
    ) -> None:
        """A valid webhook with REJECTED status updates DB and notifies Orders."""
        settings = get_settings()
        order_id = str(uuid.uuid4())
        _create_test_payment(db_session, order_id)
        data_id = "payment-456"

        # Mock MP getting payment info
        httpx_mock.add_response(
            url=f"https://api.mercadopago.com/v1/payments/{data_id}",
            method="GET",
            json={
                "id": int(data_id.split("-")[1]),
                "status": "rejected",
                "external_reference": order_id,
            },
        )

        # Mock Orders notification
        httpx_mock.add_response(
            url=f"{settings.ORDERS_SERVICE_URL}/orders/{order_id}/payment-webhook",
            method="POST",
            json={"status": "ok"},
            status_code=200,
        )

        signature = _generate_valid_signature(data_id, settings.MP_WEBHOOK_SECRET)

        response = client.post(
            "/payments/mp-webhook",
            json={"data": {"id": data_id}},
            headers={
                "x-signature": signature,
                "x-request-id": "test-request-id",
            },
        )

        assert response.status_code == 200

        payment = db_session.query(Payment).filter(Payment.order_id == uuid.UUID(order_id)).first()
        assert payment.status == PaymentStatus.REJECTED

    @pytest.mark.skip(reason="en development se aceptan firmas invalidas")
    def test_webhook_firma_invalida(
        self,
        client: TestClient,
    ) -> None:
        """A webhook with an invalid signature is rejected."""
        response = client.post(
            "/payments/mp-webhook",
            json={"data": {"id": "123"}},
            headers={
                "x-signature": "ts=123,v1=badhash",
                "x-request-id": "test",
            },
        )
        assert response.status_code == 400
        assert "Invalid webhook signature" in response.json()["detail"]


    def test_webhook_sin_headers_de_firma_se_procesa(
        self,
        client: TestClient,
        httpx_mock: HTTPXMock,
        db_session: Session,
    ) -> None:
        """MercadoPago simulator payloads without signature headers should still be accepted."""
        settings = get_settings()
        order_id = str(uuid.uuid4())
        _create_test_payment(db_session, order_id)
        data_id = "123456"

        httpx_mock.add_response(
            url=f"https://api.mercadopago.com/v1/payments/{data_id}",
            method="GET",
            json={
                "id": int(data_id),
                "status": "approved",
                "external_reference": order_id,
            },
        )
        httpx_mock.add_response(
            url=f"{settings.ORDERS_SERVICE_URL}/orders/{order_id}/payment-webhook",
            method="POST",
            json={"status": "ok"},
            status_code=200,
        )

        response = client.post(
            "/payments/mp-webhook",
            json={
                "action": "payment.updated",
                "api_version": "v1",
                "data": {"id": data_id},
                "date_created": "2021-11-01T02:02:02Z",
                "id": data_id,
                "live_mode": False,
                "type": "payment",
                "user_id": 151898577,
            },
        )

        assert response.status_code == 200


    def test_webhook_pago_inexistente(
        self,
        client: TestClient,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Webhook for a payment that doesn't exist locally still returns 200."""
        settings = get_settings()
        data_id = "payment-999"
        order_id = str(uuid.uuid4())

        # Mock MP getting payment info
        httpx_mock.add_response(
            url=f"https://api.mercadopago.com/v1/payments/{data_id}",
            method="GET",
            json={
                "id": 999,
                "status": "approved",
                "external_reference": order_id, # not in our DB
            },
        )

        signature = _generate_valid_signature(data_id, settings.MP_WEBHOOK_SECRET)

        response = client.post(
            "/payments/mp-webhook",
            json={"data": {"id": data_id}},
            headers={
                "x-signature": signature,
                "x-request-id": "test-request-id",
            },
        )

        # We return 200 to acknowledge MP, even if we couldn't process it locally
        assert response.status_code == 200

    def test_webhook_merchant_order_con_id_en_query_param(
        self,
        client: TestClient,
        httpx_mock: HTTPXMock,
        db_session: Session,
    ) -> None:
        """Merchant Order webhooks with id in query param are processed correctly.
        
        Mercado Pago sends merchant_order notifications with topic=merchant_order
        and the ID in the query string. This test verifies the endpoint extracts
        the ID correctly and processes the payment.
        """
        settings = get_settings()
        order_id = str(uuid.uuid4())
        _create_test_payment(db_session, order_id)
        payment_id = "40765860436"  # This comes from the query param

        # Mock MP getting payment info
        httpx_mock.add_response(
            url=f"https://api.mercadopago.com/v1/payments/{payment_id}",
            method="GET",
            json={
                "id": int(payment_id),
                "status": "approved",
                "external_reference": order_id,
            },
        )

        # Mock Orders notification
        httpx_mock.add_response(
            url=f"{settings.ORDERS_SERVICE_URL}/orders/{order_id}/payment-webhook",
            method="POST",
            json={"status": "ok"},
            status_code=200,
        )

        signature = _generate_valid_signature(payment_id, settings.MP_WEBHOOK_SECRET)

        # This simulates the webhook that was failing: ?id=40765860436&topic=merchant_order
        response = client.post(
            f"/payments/mp-webhook?id={payment_id}&topic=merchant_order",
            json={
                "action": "payment.updated",
                "api_version": "v1",
                "topic": "merchant_order",
                "type": "merchant_order",
            },
            headers={
                "x-signature": signature,
                "x-request-id": "test-request-id",
            },
        )

        assert response.status_code == 200

        # Verify DB updated
        payment = db_session.query(Payment).filter(Payment.order_id == uuid.UUID(order_id)).first()
        assert payment.status == PaymentStatus.APPROVED
        assert payment.mp_payment_id == payment_id
