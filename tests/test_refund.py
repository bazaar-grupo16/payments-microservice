"""
Integration tests for ``POST /payments/{order_id}/refund``.

Tests the per-item refund endpoint, covering:
- Successful partial refund (single item)
- Successful full refund (all items → payment marked REFUNDED)
- Payment not found → 404
- Item not found in payment → 404
- Item already refunded → 409
- Payment not in APPROVED status → 422
- Missing order_item_id in body → 422 (Pydantic validation)
- No mp_payment_id on payment → 502
"""

import uuid
from decimal import Decimal

from fastapi.testclient import TestClient
from pytest_httpx import HTTPXMock
from sqlalchemy.orm import Session

from src.models.payment import Payment, PaymentStatus
from src.core.config import get_settings


# ── Helpers ────────────────────────────────────────────────────────────────

ITEM_ID_1 = "item-aaa-111"
ITEM_ID_2 = "item-bbb-222"

_SINGLE_ITEM = [
    {
        "title": "Remera",
        "quantity": 2,
        "unit_price": 50.00,
        "line_item_id": ITEM_ID_1,
        "refunded": False,
    }
]

_TWO_ITEMS = [
    {
        "title": "Remera",
        "quantity": 2,
        "unit_price": 50.00,
        "line_item_id": ITEM_ID_1,
        "refunded": False,
    },
    {
        "title": "Pantalón",
        "quantity": 1,
        "unit_price": 80.00,
        "line_item_id": ITEM_ID_2,
        "refunded": False,
    },
]


def _create_payment(
    db: Session,
    order_id: str,
    *,
    status: PaymentStatus = PaymentStatus.APPROVED,
    items: list[dict] | None = None,
    mp_payment_id: str | None = "MP-PAY-12345",
) -> Payment:
    """Create a payment in the DB with the given parameters."""
    if items is None:
        items = _SINGLE_ITEM[:]  # default: one item

    payment = Payment(
        order_id=uuid.UUID(order_id),
        idempotency_key=str(uuid.uuid4()),
        amount=Decimal("100.00"),
        items=items,
        status=status,
        mp_preference_id="PREF-TEST",
        mp_payment_id=mp_payment_id,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


def _mp_refund_response(amount: float, refund_id: str = "REF-123") -> dict:
    return {
        "id": refund_id,
        "payment_id": 12345,
        "amount": amount,
        "status": "approved",
    }


# ── Endpoint tests ─────────────────────────────────────────────────────────


class TestRefundByItem:
    """Tests for ``POST /payments/{order_id}/refund``."""

    def test_single_item_refund_success(
        self,
        client: TestClient,
        httpx_mock: HTTPXMock,
        db_session: Session,
    ) -> None:
        """Refunding the only item marks it as refunded and upgrades payment to REFUNDED."""
        order_id = str(uuid.uuid4())
        _create_payment(db_session, order_id, items=list(_SINGLE_ITEM))

        # Expected amount: 2 × 50.00 = 100.00
        httpx_mock.add_response(
            url="https://api.mercadopago.com/v1/payments/MP-PAY-12345/refunds",
            method="POST",
            json=_mp_refund_response(100.00),
            status_code=201,
        )

        response = client.post(
            f"/payments/{order_id}/refund",
            json={"order_item_id": ITEM_ID_1},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["order_id"] == order_id
        assert body["order_item_id"] == ITEM_ID_1
        assert body["refund_id"] == "REF-123"
        assert body["status"] == "approved"
        assert body["amount"] == 100.00

        # All items refunded → payment should be REFUNDED
        payment = db_session.query(Payment).filter(
            Payment.order_id == uuid.UUID(order_id)
        ).first()
        assert payment.status == PaymentStatus.REFUNDED
        assert payment.items[0]["refunded"] is True

    def test_partial_refund_leaves_payment_approved(
        self,
        client: TestClient,
        httpx_mock: HTTPXMock,
        db_session: Session,
    ) -> None:
        """Refunding one item out of two keeps payment in APPROVED; only that item is marked refunded."""
        import copy
        order_id = str(uuid.uuid4())
        _create_payment(db_session, order_id, items=copy.deepcopy(_TWO_ITEMS))

        # Refund item 1: 2 × 50.00 = 100.00
        httpx_mock.add_response(
            url="https://api.mercadopago.com/v1/payments/MP-PAY-12345/refunds",
            method="POST",
            json=_mp_refund_response(100.00, "REF-PARTIAL"),
            status_code=201,
        )

        response = client.post(
            f"/payments/{order_id}/refund",
            json={"order_item_id": ITEM_ID_1},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["order_item_id"] == ITEM_ID_1
        assert body["amount"] == 100.00

        # Only one item refunded → payment stays APPROVED
        payment = db_session.query(Payment).filter(
            Payment.order_id == uuid.UUID(order_id)
        ).first()
        assert payment.status == PaymentStatus.APPROVED
        item_1 = next(i for i in payment.items if i["line_item_id"] == ITEM_ID_1)
        item_2 = next(i for i in payment.items if i["line_item_id"] == ITEM_ID_2)
        assert item_1["refunded"] is True
        assert item_2.get("refunded") is not True

    def test_all_items_refunded_marks_payment_as_refunded(
        self,
        client: TestClient,
        httpx_mock: HTTPXMock,
        db_session: Session,
    ) -> None:
        """When all items are refunded the payment status becomes REFUNDED."""
        import copy
        order_id = str(uuid.uuid4())
        # Pre-mark item 1 as already refunded so only item 2 remains
        items = copy.deepcopy(_TWO_ITEMS)
        items[0]["refunded"] = True
        _create_payment(db_session, order_id, items=items)

        # Refund item 2: 1 × 80.00 = 80.00
        httpx_mock.add_response(
            url="https://api.mercadopago.com/v1/payments/MP-PAY-12345/refunds",
            method="POST",
            json=_mp_refund_response(80.00, "REF-LAST"),
            status_code=201,
        )

        response = client.post(
            f"/payments/{order_id}/refund",
            json={"order_item_id": ITEM_ID_2},
        )

        assert response.status_code == 200
        assert response.json()["amount"] == 80.00

        payment = db_session.query(Payment).filter(
            Payment.order_id == uuid.UUID(order_id)
        ).first()
        assert payment.status == PaymentStatus.REFUNDED

    # ── Error cases ────────────────────────────────────────────────────────

    def test_missing_order_item_id_returns_422(
        self,
        client: TestClient,
    ) -> None:
        """Omitting order_item_id from the body triggers Pydantic validation → 422."""
        order_id = str(uuid.uuid4())
        response = client.post(f"/payments/{order_id}/refund", json={})
        assert response.status_code == 422

    def test_payment_not_found_returns_404(
        self,
        client: TestClient,
    ) -> None:
        """Refund request for an order with no payment returns 404."""
        order_id = str(uuid.uuid4())
        response = client.post(
            f"/payments/{order_id}/refund",
            json={"order_item_id": ITEM_ID_1},
        )
        assert response.status_code == 404
        assert "No payment found" in response.json()["detail"]

    def test_item_not_in_payment_returns_404(
        self,
        client: TestClient,
        db_session: Session,
    ) -> None:
        """Refund request for an item that doesn't exist in the payment returns 404."""
        order_id = str(uuid.uuid4())
        _create_payment(db_session, order_id)

        response = client.post(
            f"/payments/{order_id}/refund",
            json={"order_item_id": "item-does-not-exist"},
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_item_already_refunded_returns_409(
        self,
        client: TestClient,
        db_session: Session,
    ) -> None:
        """Attempting to refund an already-refunded item returns 409 Conflict."""
        import copy
        order_id = str(uuid.uuid4())
        items = copy.deepcopy(_SINGLE_ITEM)
        items[0]["refunded"] = True
        _create_payment(db_session, order_id, items=items)

        response = client.post(
            f"/payments/{order_id}/refund",
            json={"order_item_id": ITEM_ID_1},
        )

        assert response.status_code == 409
        assert "already been refunded" in response.json()["detail"]

    def test_payment_not_approved_returns_422(
        self,
        client: TestClient,
        db_session: Session,
    ) -> None:
        """Refund on a PENDING payment returns 422 Unprocessable Entity."""
        order_id = str(uuid.uuid4())
        _create_payment(db_session, order_id, status=PaymentStatus.PENDING)

        response = client.post(
            f"/payments/{order_id}/refund",
            json={"order_item_id": ITEM_ID_1},
        )

        assert response.status_code == 422
        assert "PENDING" in response.json()["detail"]

    def test_payment_rejected_returns_422(
        self,
        client: TestClient,
        db_session: Session,
    ) -> None:
        """Refund on a REJECTED payment returns 422."""
        order_id = str(uuid.uuid4())
        _create_payment(db_session, order_id, status=PaymentStatus.REJECTED)

        response = client.post(
            f"/payments/{order_id}/refund",
            json={"order_item_id": ITEM_ID_1},
        )

        assert response.status_code == 422

    def test_payment_without_mp_payment_id_returns_502(
        self,
        client: TestClient,
        db_session: Session,
    ) -> None:
        """Payment without an mp_payment_id cannot be refunded → 502."""
        order_id = str(uuid.uuid4())
        _create_payment(db_session, order_id, mp_payment_id=None)

        response = client.post(
            f"/payments/{order_id}/refund",
            json={"order_item_id": ITEM_ID_1},
        )

        assert response.status_code == 502
        assert "MercadoPago" in response.json()["detail"]

    def test_mercadopago_error_returns_502(
        self,
        client: TestClient,
        httpx_mock: HTTPXMock,
        db_session: Session,
    ) -> None:
        """When MercadoPago returns an error the payment state is NOT modified."""
        order_id = str(uuid.uuid4())
        _create_payment(db_session, order_id)

        httpx_mock.add_response(
            url="https://api.mercadopago.com/v1/payments/MP-PAY-12345/refunds",
            method="POST",
            status_code=500,
        )

        response = client.post(
            f"/payments/{order_id}/refund",
            json={"order_item_id": ITEM_ID_1},
        )

        assert response.status_code == 502

        # Payment should remain APPROVED and item should NOT be marked refunded
        payment = db_session.query(Payment).filter(
            Payment.order_id == uuid.UUID(order_id)
        ).first()
        assert payment.status == PaymentStatus.APPROVED
        assert payment.items[0].get("refunded") is not True


# ── Webhook tests (unchanged behavior) ────────────────────────────────────


class TestRefundWebhook:
    """Tests for handling refund webhooks from MercadoPago.

    These tests validate that a ``refunded`` status from MP correctly
    updates the local payment to REFUNDED via the webhook flow.
    """

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
        assert body == {"status": "REFUNDED", "transaction_id": mp_payment_id}
