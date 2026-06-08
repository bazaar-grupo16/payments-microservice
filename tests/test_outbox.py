import uuid
import pytest
from fastapi.testclient import TestClient
from pytest_httpx import HTTPXMock
from sqlalchemy.orm import Session

from src.core.database import SessionLocal
from src.core.config import get_settings
from src.models.outbox_event import OutboxEvent
from src.models.payment import Payment, PaymentStatus
from src.repositories import outbox_repository
from src.services.outbox_worker import run_outbox_worker
from tests.test_webhook import _generate_valid_signature, _create_test_payment


def test_insert_outbox_event(db_session: Session) -> None:
    """Verifies that an outbox event is successfully inserted in PENDING state."""
    payload = {"order_id": "test-order-id", "status": "APPROVED", "transaction_id": "tx-123"}
    event = outbox_repository.insert(db_session, "NOTIFY_PAYMENT_RESULT", payload)
    db_session.commit()
    
    assert event.id is not None
    assert event.status == "PENDING"
    assert event.event_type == "NOTIFY_PAYMENT_RESULT"
    assert event.payload == payload
    assert event.attempts == 0


def test_claim_batch_skip_locked(db_session: Session) -> None:
    """Verifies that claim_batch correctly locks and skips locked rows for concurrent workers."""
    # Insert a pending event
    outbox_repository.insert(db_session, "NOTIFY_PAYMENT_RESULT", {"order_id": "1"})
    db_session.commit()
    
    session1 = SessionLocal()
    session2 = SessionLocal()
    try:
        # Start a transaction in session1 and lock the row
        events_s1 = (
            session1.query(OutboxEvent)
            .filter(OutboxEvent.status == "PENDING")
            .with_for_update(skip_locked=True)
            .all()
        )
        assert len(events_s1) == 1
        
        # Call claim_batch in session2. Since session1 holds the lock, session2 should return empty
        events_s2 = outbox_repository.claim_batch(session2, batch_size=5)
        assert len(events_s2) == 0
    finally:
        session1.rollback()
        session2.rollback()
        session1.close()
        session2.close()


@pytest.mark.asyncio
async def test_worker_success(db_session: Session, httpx_mock: HTTPXMock) -> None:
    """Verifies that the worker successfully dispatches an event and marks it DONE."""
    settings = get_settings()
    order_id = str(uuid.uuid4())
    payload = {"order_id": order_id, "status": "APPROVED", "transaction_id": "tx-123"}
    
    # Insert event
    event = outbox_repository.insert(db_session, "NOTIFY_PAYMENT_RESULT", payload)
    db_session.commit()
    
    # Mock orders client endpoint
    httpx_mock.add_response(
        url=f"{settings.ORDERS_SERVICE_URL}/orders/{order_id}/payment-webhook",
        method="POST",
        json={"status": "ok"},
        status_code=200,
    )
    
    # Run one worker iteration
    await run_outbox_worker(limit_iterations=1)
    
    # Verify event is marked DONE
    db_session.refresh(event)
    assert event.status == "DONE"
    assert event.processed_at is not None


@pytest.mark.asyncio
async def test_worker_transient_failure(db_session: Session, httpx_mock: HTTPXMock) -> None:
    """Verifies that a transient HTTP failure sets status back to PENDING and increments attempts."""
    settings = get_settings()
    order_id = str(uuid.uuid4())
    payload = {"order_id": order_id, "status": "APPROVED", "transaction_id": "tx-123"}
    
    # Insert event
    event = outbox_repository.insert(db_session, "NOTIFY_PAYMENT_RESULT", payload)
    db_session.commit()
    
    # Mock orders client returning 503 Service Unavailable (3 times, as it retries 3 times internally)
    for _ in range(3):
        httpx_mock.add_response(
            url=f"{settings.ORDERS_SERVICE_URL}/orders/{order_id}/payment-webhook",
            method="POST",
            status_code=503,
        )
    
    # Run worker iteration, bypassing sleep to keep tests fast
    import unittest.mock
    with unittest.mock.patch("asyncio.sleep", return_value=None):
        await run_outbox_worker(limit_iterations=1)
    
    # Verify event is back to PENDING with attempts=1
    db_session.refresh(event)
    assert event.status == "PENDING"
    assert event.attempts == 1
    assert event.locked_at is None


@pytest.mark.asyncio
async def test_worker_permanent_failure(db_session: Session, httpx_mock: HTTPXMock) -> None:
    """Verifies that after max_attempts failure, the event status is marked FAILED."""
    settings = get_settings()
    order_id = str(uuid.uuid4())
    payload = {"order_id": order_id, "status": "APPROVED", "transaction_id": "tx-123"}
    
    # Insert event with pre-existing attempts
    event = outbox_repository.insert(db_session, "NOTIFY_PAYMENT_RESULT", payload)
    event.attempts = settings.OUTBOX_MAX_ATTEMPTS - 1
    db_session.commit()
    
    # Mock orders client returning error (3 times, as it retries 3 times internally)
    for _ in range(3):
        httpx_mock.add_response(
            url=f"{settings.ORDERS_SERVICE_URL}/orders/{order_id}/payment-webhook",
            method="POST",
            status_code=500,
        )
    
    # Run worker iteration, bypassing sleep to keep tests fast
    import unittest.mock
    with unittest.mock.patch("asyncio.sleep", return_value=None):
        await run_outbox_worker(limit_iterations=1)
    
    # Verify event is marked FAILED
    db_session.refresh(event)
    assert event.status == "FAILED"
    assert event.attempts == settings.OUTBOX_MAX_ATTEMPTS
    assert event.processed_at is not None


def test_webhook_payment_writes_outbox_event(client: TestClient, db_session: Session, httpx_mock: HTTPXMock) -> None:
    """Verifies that incoming webhooks write an OutboxEvent inside the same payment update transaction."""
    settings = get_settings()
    order_id = str(uuid.uuid4())
    _create_test_payment(db_session, order_id)
    data_id = "payment-123"
    
    # Mock MP payment info GET
    httpx_mock.add_response(
        url=f"https://api.mercadopago.com/v1/payments/{data_id}",
        method="GET",
        json={
            "id": 123,
            "status": "approved",
            "external_reference": order_id,
        },
    )

    # Mock Orders webhook notification (called during inline dispatch)
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
    
    # Check that payment status is APPROVED
    payment = db_session.query(Payment).filter(Payment.order_id == uuid.UUID(order_id)).first()
    assert payment.status == PaymentStatus.APPROVED
    
    # Check that an OutboxEvent was created in the database
    events = db_session.query(OutboxEvent).all()
    assert len(events) == 1
    assert events[0].event_type == "NOTIFY_PAYMENT_RESULT"
    assert events[0].payload == {
        "order_id": order_id,
        "status": "APPROVED",
        "transaction_id": data_id,
    }
