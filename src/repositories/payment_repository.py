"""
Data-access layer for the ``payments`` table.

Each function performs a single, focused database operation.
No business logic lives here — that belongs in the service layer.
"""

import logging
from decimal import Decimal
from uuid import UUID

from sqlalchemy.orm import Session

from src.models.payment import Payment, PaymentStatus

logger = logging.getLogger(__name__)


def create_payment(
    db: Session,
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
    db.commit()
    db.refresh(payment)
    logger.info(
        "Created payment %s for order %s (key=%s)",
        payment.id,
        order_id,
        idempotency_key,
    )
    return payment


def get_by_idempotency_key(db: Session, key: str) -> Payment | None:
    """Look up a payment by its idempotency key."""
    return db.query(Payment).filter(Payment.idempotency_key == key).first()


def get_by_order_id(db: Session, order_id: UUID) -> Payment | None:
    """Look up the most recent payment for a given order."""
    return (
        db.query(Payment)
        .filter(Payment.order_id == order_id)
        .order_by(Payment.created_at.desc())
        .first()
    )


def get_by_mp_preference_id(db: Session, preference_id: str) -> Payment | None:
    """Look up a payment by its MercadoPago preference ID."""
    return (
        db.query(Payment)
        .filter(Payment.mp_preference_id == preference_id)
        .first()
    )


def get_by_mp_payment_id(db: Session, mp_payment_id: str) -> Payment | None:
    """Look up a payment by its MercadoPago payment ID."""
    return (
        db.query(Payment)
        .filter(Payment.mp_payment_id == mp_payment_id)
        .first()
    )


def update_with_preference(
    db: Session,
    payment: Payment,
    *,
    mp_preference_id: str,
    init_point: str,
) -> Payment:
    """Store the MercadoPago preference details on an existing payment."""
    payment.mp_preference_id = mp_preference_id
    payment.init_point = init_point
    db.commit()
    db.refresh(payment)
    logger.info(
        "Updated payment %s with preference %s",
        payment.id,
        mp_preference_id,
    )
    return payment


def update_status(
    db: Session,
    payment: Payment,
    *,
    status: PaymentStatus,
    mp_payment_id: str | None = None,
) -> Payment:
    """Update the payment status (and optionally the MP payment ID)."""
    payment.status = status
    if mp_payment_id is not None:
        payment.mp_payment_id = mp_payment_id
    db.commit()
    db.refresh(payment)
    logger.info(
        "Payment %s status → %s (mp_payment_id=%s)",
        payment.id,
        status.value,
        mp_payment_id,
    )
    return payment


def mark_item_as_refunded(
    db: Session,
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
    db.commit()
    db.refresh(payment)
    logger.info(
        "Marked item %s as refunded in payment %s",
        order_item_id,
        payment.id,
    )
    return payment
