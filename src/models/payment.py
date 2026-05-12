"""
SQLAlchemy model for the ``payments`` table.

Each row represents a single payment intent linked to an order.
The table lives in the Payments service's **own** database —
it is never shared with other microservices (Red Line).
"""

import enum
import uuid

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Numeric,
    String,
)
from sqlalchemy.dialects.postgresql import JSON, UUID

from src.core.database import Base
from src.core.utils import utc_now


class PaymentStatus(str, enum.Enum):
    """Possible states for a payment."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CANCELED = "CANCELED"
    REFUNDED = "REFUNDED"


class Payment(Base):
    """Persistent representation of a MercadoPago payment intent."""

    __tablename__ = "payments"

    # ── Primary key ──────────────────────────────────
    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        nullable=False,
    )

    # ── Business identifiers ─────────────────────────
    order_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    idempotency_key = Column(
        String(36), unique=True, nullable=False, index=True
    )

    # ── MercadoPago data ─────────────────────────────
    mp_preference_id = Column(String(255), nullable=True)
    init_point = Column(String(512), nullable=True)
    mp_payment_id = Column(String(255), nullable=True)

    # ── Payment details ──────────────────────────────
    status = Column(
        Enum(PaymentStatus, name="payment_status"),
        nullable=False,
        default=PaymentStatus.PENDING,
    )
    amount = Column(Numeric(12, 2), nullable=True)
    items = Column(JSON, nullable=True)

    # ── Timestamps ───────────────────────────────────
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<Payment id={self.id} order={self.order_id} "
            f"status={self.status}>"
        )
