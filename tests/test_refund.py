"""
Integration tests for ``POST /payments/{order_id}/refund``.
"""

import uuid
from decimal import Decimal

from fastapi.testclient import TestClient
from pytest_httpx import HTTPXMock
from sqlalchemy.orm import Session

from src.models.payment import Payment, PaymentStatus
from src.core.config import get_settings


def _create_approved_payment(db: Session, order_id: str) -> Payment:
    """Create an approved payment in the DB."""
    payment = Payment(
        order_id=uuid.UUID(order_id),
        idempotency_key=str(uuid.uuid4()),
        amount=Decimal("100.00"),
        items=[{"title": "Test item", "quantity": 1, "unit_price": 100.00}],
        status=PaymentStatus.APPROVED,
        mp_preference_id="PREF-TEST",
        mp_payment_id="MP-PAY-12345",
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


class TestRefundEndpoint:
    """Tests for ``POST /payments/{order_id}/refund``."""

    def test_full_refund_success(
        self,
        client: TestClient,
        httpx_mock: HTTPXMock,
        db_session: Session,
    ) -> None:
        """A full refund request updates DB to CANCELED and calls MercadoPago."""
        order_id = str(uuid.uuid4())
        _create_approved_payment(db_session, order_id)

        # Mock MercadoPago refund endpoint
        httpx_mock.add_response(
            url="https://api.mercadopago.com/v1/payments/MP-PAY-12345/refunds",
            method="POST",
            json={
                "id": "REF-123",
                "payment_id": 12345,
                "amount": 100.00,
                "status": "approved",
            },
            status_code=201,
        )

        response = client.post(
            f"/payments/{order_id}/refund",
            json={},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["order_id"] == order_id
        assert body["refund_id"] == "REF-123"
        assert body["status"] == "approved"
        assert body["amount"] == 100.00

        # Verify DB updated
        payment = db_session.query(Payment).filter(
            Payment.order_id == uuid.UUID(order_id)
        ).first()
        assert payment.status == PaymentStatus.CANCELED

    def test_partial_refund_success(
        self,
        client: TestClient,
        httpx_mock: HTTPXMock,
        db_session: Session,
    ) -> None:
        """A partial refund request sends amount to MercadoPago."""
        order_id = str(uuid.uuid4())
        _create_approved_payment(db_session, order_id)

        # Mock MercadoPago refund endpoint
        httpx_mock.add_response(
            url="https://api.mercadopago.com/v1/payments/MP-PAY-12345/refunds",
            method="POST",
            json={
                "id": "REF-456",
                "payment_id": 12345,
                "amount": 50.00,
                "status": "approved",
            },
            status_code=201,
        )

        response = client.post(
            f"/payments/{order_id}/refund",
            json={"amount": 50.00},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["refund_id"] == "REF-456"
        assert body["amount"] == 50.00

        # Verify DB updated to CANCELED (not REFUNDED, that happens on webhook)
        payment = db_session.query(Payment).filter(
            Payment.order_id == uuid.UUID(order_id)
        ).first()
        assert payment.status == PaymentStatus.CANCELED

    def test_refund_nonexistent_payment_returns_404(
        self,
        client: TestClient,
    ) -> None:
        """A refund request for nonexistent payment returns 404."""
        order_id = str(uuid.uuid4())

        response = client.post(
            f"/payments/{order_id}/refund",
            json={},
        )

        assert response.status_code == 404
        assert "No payment found" in response.json()["detail"]

    def test_refund_already_refunded_payment(
        self,
        client: TestClient,
        db_session: Session,
    ) -> None:
        """A refund request for already refunded payment returns success without calling MP."""
        order_id = str(uuid.uuid4())
        payment = Payment(
            order_id=uuid.UUID(order_id),
            idempotency_key=str(uuid.uuid4()),
            amount=Decimal("100.00"),
            items=[],
            status=PaymentStatus.REFUNDED,
            mp_preference_id="PREF-TEST",
            mp_payment_id="MP-PAY-99999",
        )
        db_session.add(payment)
        db_session.commit()
        db_session.refresh(payment)

        # Should not call MercadoPago
        response = client.post(
            f"/payments/{order_id}/refund",
            json={},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "already_refunded"
        assert body["order_id"] == order_id

    def test_refund_payment_without_mp_payment_id(
        self,
        client: TestClient,
        db_session: Session,
    ) -> None:
        """A refund request for payment without mp_payment_id fails."""
        order_id = str(uuid.uuid4())
        payment = Payment(
            order_id=uuid.UUID(order_id),
            idempotency_key=str(uuid.uuid4()),
            amount=Decimal("100.00"),
            items=[],
            status=PaymentStatus.APPROVED,
            mp_preference_id="PREF-TEST",
            mp_payment_id=None,  # No payment ID
        )
        db_session.add(payment)
        db_session.commit()

        response = client.post(
            f"/payments/{order_id}/refund",
            json={},
        )

        assert response.status_code == 502
        assert "MercadoPago" in response.json()["detail"]


class TestRefundWebhook:
    """Tests for handling refund webhooks from MercadoPago."""

    def test_refund_status_updates_payment_to_refunded(
        self,
        client: TestClient,
        httpx_mock: HTTPXMock,
        db_session: Session,
    ) -> None:
        """When MercadoPago sends refunded status via webhook, payment is updated to REFUNDED."""
        settings = get_settings()
        order_id = str(uuid.uuid4())
        mp_payment_id = "999999"

        # Create a CANCELED payment (after refund request)
        payment = Payment(
            order_id=uuid.UUID(order_id),
            idempotency_key=str(uuid.uuid4()),
            amount=Decimal("100.00"),
            items=[],
            status=PaymentStatus.CANCELED,
            mp_preference_id="PREF-TEST",
            mp_payment_id=mp_payment_id,
        )
        db_session.add(payment)
        db_session.commit()

        # Mock MP get payment (returns refunded status)
        httpx_mock.add_response(
            url=f"https://api.mercadopago.com/v1/payments/{mp_payment_id}",
            method="GET",
            json={
                "id": int(mp_payment_id),
                "status": "refunded",
                "external_reference": order_id,
            },
            status_code=200,
        )

        # Mock Orders notification
        httpx_mock.add_response(
            url=f"{settings.ORDERS_SERVICE_URL}/orders/{order_id}/payment-webhook",
            method="POST",
            json={"status": "ok"},
            status_code=200,
        )

        # Generate valid webhook signature
        from tests.test_webhook import _generate_valid_signature
        signature = _generate_valid_signature(mp_payment_id, settings.MP_WEBHOOK_SECRET)

        # Send refund webhook
        response = client.post(
            "/payments/mp-webhook",
            json={"action": "payment.updated", "data": {"id": mp_payment_id}},
            headers={
                "x-signature": signature,
                "x-request-id": "test-request-id",
            },
        )

        assert response.status_code == 200

        # Verify DB updated to REFUNDED
        payment = db_session.query(Payment).filter(
            Payment.order_id == uuid.UUID(order_id)
        ).first()
        assert payment.status == PaymentStatus.REFUNDED

        # Verify Orders service was notified
        requests = httpx_mock.get_requests()
        orders_req = next(r for r in requests if "orders" in str(r.url))
        import json
        body = json.loads(orders_req.content)
        assert body == {"status": "FAILED", "transaction_id": mp_payment_id}
