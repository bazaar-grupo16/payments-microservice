"""
Data-access layer for the ``outbox_events`` table.
"""

import logging
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.outbox_event import OutboxEvent
from src.core.utils import utc_now

logger = logging.getLogger(__name__)


async def insert(session: AsyncSession, event_type: str, payload: dict) -> OutboxEvent:
    """Insert a new OutboxEvent in PENDING status.
    
    This function MUST NOT call commit.
    """
    event = OutboxEvent(
        event_type=event_type,
        payload=payload,
        status="PENDING",
        attempts=0,
    )
    session.add(event)
    logger.info(
        "Inserted outbox event type=%s payload=%s into session",
        event_type,
        payload,
    )
    return event


async def claim_batch(session: AsyncSession, batch_size: int) -> list[OutboxEvent]:
    """Atomically claim up to batch_size PENDING events using FOR UPDATE SKIP LOCKED.
    
    Sets status = 'PROCESSING' and locked_at = now() on claimed rows,
    then commits the transaction immediately.
    """
    stmt = (
        select(OutboxEvent)
        .filter(OutboxEvent.status == "PENDING")
        .order_by(OutboxEvent.created_at.asc())
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    result = await session.execute(stmt)
    events = list(result.scalars().all())
    
    if not events:
        return []
        
    now = utc_now()
    for event in events:
        event.status = "PROCESSING"
        event.locked_at = now
        
    await session.commit()
    logger.info("Claimed batch of %d outbox events", len(events))
    return events


async def mark_done(session: AsyncSession, event_id: UUID) -> None:
    """Set the event status to DONE and processed_at to now, then commits."""
    stmt = select(OutboxEvent).filter(OutboxEvent.id == event_id)
    result = await session.execute(stmt)
    event = result.scalar_one_or_none()
    if event:
        event.status = "DONE"
        event.processed_at = utc_now()
        await session.commit()
        logger.info("Marked outbox event %s as DONE", event_id)


async def mark_failed(session: AsyncSession, event_id: UUID, max_attempts: int) -> None:
    """Increment attempts. If attempts < max_attempts resets to PENDING, else sets status to FAILED."""
    stmt = select(OutboxEvent).filter(OutboxEvent.id == event_id)
    result = await session.execute(stmt)
    event = result.scalar_one_or_none()
    if event:
        event.attempts += 1
        if event.attempts < max_attempts:
            event.status = "PENDING"
            event.locked_at = None
            logger.info(
                "Outbox event %s failed (attempt %d/%d), resetting to PENDING",
                event_id,
                event.attempts,
                max_attempts,
            )
        else:
            event.status = "FAILED"
            event.processed_at = utc_now()
            logger.error(
                "Outbox event %s failed permanently after %d attempts",
                event_id,
                event.attempts,
            )
        await session.commit()
