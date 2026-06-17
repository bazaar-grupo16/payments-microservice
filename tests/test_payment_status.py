"""
Integration tests for ``GET /payments/{order_id}/status``.
"""

import uuid
from decimal import Decimal

from httpx import AsyncClient
from pytest_httpx import HTTPXMock
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.payment import Payment, PaymentStatus
from src.core.config import get_settings
from tests.test_webhook import _generate_valid_signature


async def _create_payment(db: AsyncSession, order_id: str, status: PaymentStatus = PaymentStatus.PENDING) -> Payment:
    """Helper to create a payment record in the DB."""
    payment = Payment(
        order_id=uuid.UUID(order_id),
        idempotency_key=str(uuid.uuid4()),
        amount=Decimal("150.00"),
        items=[{"title": "Test item", "quantity": 1, "unit_price": 150.00}],
        status=status,
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    return payment


class TestGetPaymentStatus:
    """Tests for ``GET /payments/{order_id}/status``."""

    async def test_get_existing_payment_status(
        self,
        async_client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        """Querying an existing payment returns 200 and the correct status."""
        order_id = str(uuid.uuid4())
        await _create_payment(db_session, order_id, PaymentStatus.APPROVED)

        response = await async_client.get(f"/payments/{order_id}/status")

        assert response.status_code == 200
        body = response.json()
        assert body["order_id"] == order_id
        assert body["payment_status"] == "APPROVED"

    async def test_get_nonexistent_payment_returns_404(
        self,
        async_client: AsyncClient,
    ) -> None:
        """Querying a payment that does not exist returns 404."""
        order_id = str(uuid.uuid4())

        response = await async_client.get(f"/payments/{order_id}/status")

        assert response.status_code == 404
        assert "No payment found" in response.json()["detail"]

    async def test_full_saga_flow(
        self,
        async_client: AsyncClient,
        httpx_mock: HTTPXMock,
        db_session: AsyncSession,
    ) -> None:
        """End-to-end test of the payment saga: Create -> Webhook -> Poll Status."""
        settings = get_settings()
        order_id = str(uuid.uuid4())
        idemp_key = str(uuid.uuid4())
        pref_id = "PREF-SAGA-123"
        data_id = "payment-987"

        # 1. Mock MP create preference
        httpx_mock.add_response(
            url="https://api.mercadopago.com/checkout/preferences",
            method="POST",
            json={"id": pref_id, "init_point": f"https://mp.com/{pref_id}"},
            status_code=201,
        )

        # Create preference request
        pref_response = await async_client.post(
            "/payments/preferences",
            json={
                "order_id": order_id,
                "items": [{"title": "Saga Item", "quantity": 1, "unit_price": 100.0, "line_item_id": "item-saga-1"}],
            },
            headers={"Idempotency-Key": idemp_key},
        )
        assert pref_response.status_code == 201

        # Verify initial status is PENDING
        status_response1 = await async_client.get(f"/payments/{order_id}/status")
        assert status_response1.status_code == 200
        assert status_response1.json()["payment_status"] == "PENDING"

        # 2. Mock MP get payment info (webhook will call this)
        httpx_mock.add_response(
            url=f"https://api.mercadopago.com/v1/payments/{data_id}",
            method="GET",
            json={
                "id": int(data_id.split("-")[1]),
                "status": "approved",
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

        # Send Webhook
        signature = _generate_valid_signature(data_id, settings.MP_WEBHOOK_SECRET)
        webhook_response = await async_client.post(
            "/payments/mp-webhook",
            json={"action": "payment.created", "data": {"id": data_id}},
            headers={
                "x-signature": signature,
                "x-request-id": "test-request-id",
            },
        )
        assert webhook_response.status_code == 200

        # 3. Poll status again, should now be APPROVED
        status_response2 = await async_client.get(f"/payments/{order_id}/status")
        assert status_response2.status_code == 200
        assert status_response2.json()["payment_status"] == "APPROVED"
