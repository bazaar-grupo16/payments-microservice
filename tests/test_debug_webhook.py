"""
Integration tests for ``POST /payments/debug``.
"""

import uuid
from decimal import Decimal

from fastapi.testclient import TestClient
from pytest_httpx import HTTPXMock
from sqlalchemy.orm import Session

from src.models.payment import Payment, PaymentStatus
from src.core.config import get_settings


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


class TestDebugWebhook:
    """Tests for ``POST /payments/debug``."""

    def test_debug_webhook_pago_aprobado(
        self,
        client: TestClient,
        httpx_mock: HTTPXMock,
        db_session: Session,
    ) -> None:
        """A valid debug webhook with accepted=True updates DB and notifies Orders."""
        settings = get_settings()
        order_id = str(uuid.uuid4())
        _create_test_payment(db_session, order_id)

        # Mock Orders notification
        httpx_mock.add_response(
            url=f"{settings.ORDERS_SERVICE_URL}/orders/{order_id}/payment-webhook",
            method="POST",
            json={"status": "ok"},
            status_code=200,
        )

        response = client.post(
            "/payments/debug",
            json={"operation_id": order_id, "accepted": True},
        )

        assert response.status_code == 200
        assert response.json()["accepted"] is True
        assert response.json()["operation_id"] == order_id

        # Verify DB updated
        payment = db_session.query(Payment).filter(Payment.order_id == uuid.UUID(order_id)).first()
        assert payment.status == PaymentStatus.APPROVED
        assert payment.mp_payment_id == "DEBUG-TX"

        # Verify Orders called
        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        orders_req = next(r for r in requests if "orders" in str(r.url))
        import json
        body = json.loads(orders_req.content)
        assert body == {"status": "SUCCESS", "transaction_id": "DEBUG-TX"}

    def test_debug_webhook_pago_rechazado(
        self,
        client: TestClient,
        httpx_mock: HTTPXMock,
        db_session: Session,
    ) -> None:
        """A valid debug webhook with accepted=False updates DB to REJECTED."""
        settings = get_settings()
        order_id = str(uuid.uuid4())
        _create_test_payment(db_session, order_id)

        # Mock Orders notification
        httpx_mock.add_response(
            url=f"{settings.ORDERS_SERVICE_URL}/orders/{order_id}/payment-webhook",
            method="POST",
            json={"status": "ok"},
            status_code=200,
        )

        response = client.post(
            "/payments/debug",
            json={"operation_id": order_id, "accepted": False},
        )

        assert response.status_code == 200

        # Verify DB updated
        payment = db_session.query(Payment).filter(Payment.order_id == uuid.UUID(order_id)).first()
        assert payment.status == PaymentStatus.REJECTED
        assert payment.mp_payment_id == "DEBUG-TX"

    def test_debug_webhook_pago_inexistente(
        self,
        client: TestClient,
    ) -> None:
        """A debug webhook for an unknown operation_id returns 404."""
        order_id = str(uuid.uuid4())

        response = client.post(
            "/payments/debug",
            json={"operation_id": order_id, "accepted": True},
        )

        assert response.status_code == 404
        assert "No payment found" in response.json()["detail"]
