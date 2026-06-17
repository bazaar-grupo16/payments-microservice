"""
Integration tests for ``POST /payments/debug``.
"""

import uuid
from decimal import Decimal

from httpx import AsyncClient
from pytest_httpx import HTTPXMock
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.payment import Payment, PaymentStatus
from src.core.config import get_settings


async def _create_test_payment(db: AsyncSession, order_id: str) -> Payment:
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
    await db.commit()
    await db.refresh(payment)
    return payment


class TestDebugWebhook:
    """Tests for ``POST /payments/debug``."""

    async def test_debug_webhook_pago_aprobado(
        self,
        async_client: AsyncClient,
        httpx_mock: HTTPXMock,
        db_session: AsyncSession,
    ) -> None:
        """A valid debug webhook with accepted=True updates DB and notifies Orders."""
        settings = get_settings()
        order_id = str(uuid.uuid4())
        await _create_test_payment(db_session, order_id)

        # Mock Orders notification
        httpx_mock.add_response(
            url=f"{settings.ORDERS_SERVICE_URL}/orders/{order_id}/payment-webhook",
            method="POST",
            json={"status": "ok"},
            status_code=200,
        )

        response = await async_client.post(
            "/payments/debug",
            json={"operation_id": order_id, "accepted": True},
        )

        assert response.status_code == 200
        assert response.json()["accepted"] is True
        assert response.json()["operation_id"] == order_id

        # Verify DB updated
        stmt = select(Payment).filter(Payment.order_id == uuid.UUID(order_id))
        result = await db_session.execute(stmt)
        payment = result.scalars().first()
        assert payment.status == PaymentStatus.APPROVED
        assert payment.mp_payment_id == "DEBUG-TX"

        # Verify Orders called
        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        orders_req = next(r for r in requests if "orders" in str(r.url))
        import json
        body = json.loads(orders_req.content)
        assert body == {"status": "APPROVED", "transaction_id": "DEBUG-TX"}

    async def test_debug_webhook_pago_rechazado(
        self,
        async_client: AsyncClient,
        httpx_mock: HTTPXMock,
        db_session: AsyncSession,
    ) -> None:
        """A valid debug webhook with accepted=False updates DB to REJECTED."""
        settings = get_settings()
        order_id = str(uuid.uuid4())
        await _create_test_payment(db_session, order_id)

        # Mock Orders notification
        httpx_mock.add_response(
            url=f"{settings.ORDERS_SERVICE_URL}/orders/{order_id}/payment-webhook",
            method="POST",
            json={"status": "ok"},
            status_code=200,
        )

        response = await async_client.post(
            "/payments/debug",
            json={"operation_id": order_id, "accepted": False},
        )

        assert response.status_code == 200

        # Verify DB updated
        stmt = select(Payment).filter(Payment.order_id == uuid.UUID(order_id))
        result = await db_session.execute(stmt)
        payment = result.scalars().first()
        assert payment.status == PaymentStatus.REJECTED
        assert payment.mp_payment_id == "DEBUG-TX"

    async def test_debug_webhook_pago_inexistente(
        self,
        async_client: AsyncClient,
    ) -> None:
        """A debug webhook for an unknown operation_id returns 404."""
        order_id = str(uuid.uuid4())

        response = await async_client.post(
            "/payments/debug",
            json={"operation_id": order_id, "accepted": True},
        )

        assert response.status_code == 404
        assert "No payment found" in response.json()["detail"]
