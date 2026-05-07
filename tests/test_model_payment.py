"""
Tests for the ``Payment`` SQLAlchemy model.

All tests run against a **real PostgreSQL** instance — no mocking.
"""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models.payment import Payment, PaymentStatus

_SAMPLE_ITEMS = [
    {"title": "Compra Bazaar #1", "quantity": 1, "unit_price": 199.99}
]


class TestPaymentModel:
    """Validate that the Payment table schema and defaults work correctly."""

    def _make_payment(self, **overrides) -> Payment:
        """Build a Payment instance with sensible defaults."""
        defaults = {
            "order_id": uuid.uuid4(),
            "idempotency_key": str(uuid.uuid4()),
            "amount": Decimal("199.99"),
            "items": _SAMPLE_ITEMS,
        }
        defaults.update(overrides)
        return Payment(**defaults)

    def test_create_payment_persists(self, db_session: Session) -> None:
        """A new Payment can be persisted and read back."""
        payment = self._make_payment()
        db_session.add(payment)
        db_session.commit()

        fetched = db_session.get(Payment, payment.id)
        assert fetched is not None
        assert fetched.order_id == payment.order_id

    def test_default_status_is_pending(self, db_session: Session) -> None:
        """The status column should default to PENDING."""
        payment = self._make_payment()
        db_session.add(payment)
        db_session.commit()

        fetched = db_session.get(Payment, payment.id)
        assert fetched.status == PaymentStatus.PENDING

    def test_default_id_is_uuid(self, db_session: Session) -> None:
        """The primary key should be auto-generated as a UUID."""
        payment = self._make_payment()
        db_session.add(payment)
        db_session.commit()

        assert payment.id is not None
        assert isinstance(payment.id, uuid.UUID)

    def test_created_at_auto_populated(self, db_session: Session) -> None:
        """created_at should be automatically set on insert."""
        payment = self._make_payment()
        db_session.add(payment)
        db_session.commit()

        assert payment.created_at is not None

    def test_unique_idempotency_key(self, db_session: Session) -> None:
        """Two payments with the same idempotency_key must raise IntegrityError."""
        key = str(uuid.uuid4())
        p1 = self._make_payment(idempotency_key=key)
        p2 = self._make_payment(idempotency_key=key)

        db_session.add(p1)
        db_session.commit()

        db_session.add(p2)
        with pytest.raises(IntegrityError):
            db_session.commit()

    def test_amount_stores_decimal(self, db_session: Session) -> None:
        """The amount column should preserve two-decimal precision."""
        payment = self._make_payment(amount=Decimal("1234.56"))
        db_session.add(payment)
        db_session.commit()
        db_session.refresh(payment)

        assert payment.amount == Decimal("1234.56")

    def test_nullable_mp_fields(self, db_session: Session) -> None:
        """mp_preference_id, init_point, mp_payment_id start as None."""
        payment = self._make_payment()
        db_session.add(payment)
        db_session.commit()

        assert payment.mp_preference_id is None
        assert payment.init_point is None
        assert payment.mp_payment_id is None

    def test_status_update(self, db_session: Session) -> None:
        """Updating the status column persists correctly."""
        payment = self._make_payment()
        db_session.add(payment)
        db_session.commit()

        payment.status = PaymentStatus.APPROVED
        db_session.commit()
        db_session.refresh(payment)

        assert payment.status == PaymentStatus.APPROVED

    def test_items_stored_as_json(self, db_session: Session) -> None:
        """The items JSON column stores and retrieves the items list."""
        items = [
            {"title": "Item A", "quantity": 2, "unit_price": 50.00},
            {"title": "Item B", "quantity": 1, "unit_price": 100.00},
        ]
        payment = self._make_payment(items=items, amount=Decimal("200.00"))
        db_session.add(payment)
        db_session.commit()
        db_session.refresh(payment)

        assert len(payment.items) == 2
        assert payment.items[0]["title"] == "Item A"
        assert payment.items[1]["unit_price"] == 100.00
