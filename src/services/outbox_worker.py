"""
Background outbox worker to process events from the outbox_events table.
"""

import asyncio
import logging
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import get_settings
from src.core.database import SessionLocal
from src.repositories import outbox_repository
from src.models.outbox_event import OutboxEvent
from src.services import orders_client
from src.core.utils import utc_now

logger = logging.getLogger(__name__)


async def dispatch_event(db: AsyncSession, event: OutboxEvent) -> None:
    """Dispatches the event and updates its database status based on outcome."""
    settings = get_settings()
    payload = event.payload or {}
    
    try:
        if event.event_type == "NOTIFY_PAYMENT_RESULT":
            success = await orders_client.notify_payment_result(
                order_id=payload["order_id"],
                status=payload["status"],
                transaction_id=payload["transaction_id"],
            )
            if not success:
                raise Exception("Payment notification returned False")
                
        elif event.event_type == "NOTIFY_REFUND_RESULT":
            success = await orders_client.notify_refund_result(
                order_id=payload["order_id"],
                order_item_id=payload["order_item_id"],
                refund_status=payload["refund_status"],
            )
            if not success:
                raise Exception("Refund notification returned False")
        else:
            raise ValueError(f"Unknown event type: {event.event_type}")
            
        # Success: mark DONE
        event.status = "DONE"
        event.processed_at = utc_now()
        await db.commit()
        logger.info("Outbox event %s successfully processed", event.id)
        
    except Exception as exc:
        logger.warning(
            "Processing failed for outbox event %s: %s",
            event.id,
            exc,
            exc_info=True
        )
        event.attempts += 1
        if event.attempts < settings.OUTBOX_MAX_ATTEMPTS:
            event.status = "PENDING"
            event.locked_at = None
        else:
            event.status = "FAILED"
            event.processed_at = utc_now()
        await db.commit()


async def process_outbox_event_inline(db: AsyncSession, event: OutboxEvent) -> None:
    """Attempt to process the outbox event inline during the request.
    
    If it succeeds, status is set to DONE.
    If it fails, it is set back to PENDING (with an incremented attempts count)
    so the background worker will retry it.
    """
    event.status = "PROCESSING"
    event.locked_at = utc_now()
    await db.commit()
    
    await dispatch_event(db, event)


async def _claim_batch_ids() -> list[UUID]:
    """Asynchronously claim a batch and return their UUIDs."""
    async with SessionLocal() as db:
        events = await outbox_repository.claim_batch(db, get_settings().OUTBOX_WORKER_BATCH_SIZE)
        return [e.id for e in events]


async def _process_single_event_by_id(event_id: UUID) -> None:
    """Asynchronous database fetch helper running on the main event loop,
    delegating event dispatch to the async dispatch_event function.
    """
    async with SessionLocal() as db:
        stmt = select(OutboxEvent).filter(OutboxEvent.id == event_id)
        result = await db.execute(stmt)
        event = result.scalar_one_or_none()
        if event:
            await dispatch_event(db, event)


async def run_outbox_worker(limit_iterations: int | None = None) -> None:
    """Run the background outbox worker loop as an async task.
    
    If limit_iterations is provided, the loop will exit after that many iterations (useful for testing).
    """
    settings = get_settings()
    logger.info("Starting outbox worker task")
    
    iterations = 0
    while True:
        # Sleep at the beginning of the iteration loop to prevent startup race conditions
        # in tests and give other tasks a chance to execute.
        if limit_iterations is None:
            await asyncio.sleep(settings.OUTBOX_WORKER_INTERVAL_SECONDS)
            
        try:
            # Run the database claim asynchronously
            event_ids = await _claim_batch_ids()
            if event_ids:
                logger.info("Outbox worker processing batch of %d events", len(event_ids))
                for event_id in event_ids:
                    await _process_single_event_by_id(event_id)
        except Exception as exc:
            logger.error("Unexpected error in outbox worker task loop: %s", exc, exc_info=True)
            
        iterations += 1
        if limit_iterations is not None and iterations >= limit_iterations:
            logger.info("Exiting outbox worker task as limit_iterations=%d reached", limit_iterations)
            break
            
        # In testing/limited iterations, we don't want to sleep or we sleep afterwards
        if limit_iterations is not None:
            await asyncio.sleep(settings.OUTBOX_WORKER_INTERVAL_SECONDS)
