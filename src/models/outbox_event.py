"""
SQLAlchemy model for the ``outbox_events`` table.
"""

import uuid
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSON, UUID

from src.core.database import Base


class OutboxEvent(Base):
    """Outbox event representing a message to be processed asynchronously."""

    __tablename__ = "outbox_events"

    # ── Primary key ──────────────────────────────────
    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
        nullable=False,
    )

    # ── Fields ───────────────────────────────────────
    event_type = Column(String, nullable=False)
    payload = Column(JSON, nullable=True)
    status = Column(String, nullable=False, default="PENDING")
    attempts = Column(Integer, nullable=False, default=0)

    # ── Timestamps ───────────────────────────────────
    locked_at = Column(DateTime(timezone=True), nullable=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    def __repr__(self) -> str:
        return (
            f"<OutboxEvent id={self.id} event_type={self.event_type} "
            f"status={self.status} attempts={self.attempts}>"
        )
