"""
Data-access layer for the ``payments`` table.

Each function performs a single, focused database operation.
No business logic lives here — that belongs in the service layer.
"""

import logging
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.payment import Payment, PaymentStatus

logger = logging.getLogger(__name__)


async def create_payment(
    db: AsyncSession,
    *,
    order_id: UUID,
    idempotency_key: str,
    amount: Decimal,
    items: list[dict],
) -> Payment:
    """Insert a new payment row in PENDING status and return it."""
    payment = Payment(
        order_id=order_id,
        idempotency_key=idempotency_key,
        amount=amount,
        items=items,
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    logger.info(
        "Created payment %s for order %s (key=%s)",
        payment.id,
        order_id,
        idempotency_key,
    )
    return payment


async def get_by_idempotency_key(db: AsyncSession, key: str) -> Payment | None:
    """Look up a payment by its idempotency key."""
    stmt = select(Payment).filter(Payment.idempotency_key == key)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_by_order_id(db: AsyncSession, order_id: UUID) -> Payment | None:
    """Look up the most recent payment for a given order."""
    stmt = (
        select(Payment)
        .filter(Payment.order_id == order_id)
        .order_by(Payment.created_at.desc())
    )
    result = await db.execute(stmt)
    return result.scalars().first()


async def get_by_mp_preference_id(db: AsyncSession, preference_id: str) -> Payment | None:
    """Look up a payment by its MercadoPago preference ID."""
    stmt = select(Payment).filter(Payment.mp_preference_id == preference_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_by_mp_payment_id(db: AsyncSession, mp_payment_id: str) -> Payment | None:
    """Look up a payment by its MercadoPago payment ID."""
    stmt = select(Payment).filter(Payment.mp_payment_id == mp_payment_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def update_with_preference(
    db: AsyncSession,
    payment: Payment,
    *,
    mp_preference_id: str,
    init_point: str,
) -> Payment:
    """Store the MercadoPago preference details on an existing payment."""
    payment.mp_preference_id = mp_preference_id
    payment.init_point = init_point
    await db.commit()
    await db.refresh(payment)
    logger.info(
        "Updated payment %s with preference %s",
        payment.id,
        mp_preference_id,
    )
    return payment


async def update_status(
    db: AsyncSession,
    payment: Payment,
    *,
    status: PaymentStatus,
    mp_payment_id: str | None = None,
) -> Payment:
    """Update the payment status (and optionally the MP payment ID)."""
    payment.status = status
    if mp_payment_id is not None:
        payment.mp_payment_id = mp_payment_id
    await db.commit()
    await db.refresh(payment)
    logger.info(
        "Payment %s status → %s (mp_payment_id=%s)",
        payment.id,
        status.value,
        mp_payment_id,
    )
    return payment


async def mark_item_as_refunded(
    db: AsyncSession,
    payment: Payment,
    order_item_id: str,
    refund_id: str | None = None,
) -> Payment:
    """Mark a specific item as refunded within the payment's JSON items list.

    Uses ``flag_modified`` to ensure SQLAlchemy detects the mutation of the
    JSON column and persists it on the next commit.

    Parameters
    ----------
    db:
        Active SQLAlchemy session.
    payment:
        The payment record to update.
    order_item_id:
        The ``line_item_id`` of the item to mark as refunded.
    refund_id:
        The ID of the refund associated with this item.

    Returns
    -------
    Payment
        The updated payment record.
    """
    import copy
    from sqlalchemy.orm.attributes import flag_modified

    updated_items = copy.deepcopy(payment.items or [])
    for item in updated_items:
        if item.get("line_item_id") == order_item_id:
            item["refunded"] = True
            if refund_id is not None:
                item["refund_id"] = refund_id
            break

    payment.items = updated_items
    flag_modified(payment, "items")
    await db.commit()
    await db.refresh(payment)
    logger.info(
        "Marked item %s as refunded in payment %s",
        order_item_id,
        payment.id,
    )
    return payment
