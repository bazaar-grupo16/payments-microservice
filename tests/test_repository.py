"""
Tests for the payment repository layer.

All tests run against a **real PostgreSQL** instance — no mocking.
"""

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.payment import PaymentStatus
from src.repositories import payment_repository as repo

_SAMPLE_ITEMS = [
    {"title": "Test product", "quantity": 1, "unit_price": 250.00}
]


class TestCreatePayment:
    """Tests for ``create_payment``."""

    async def test_creates_and_returns_payment(self, db_session: AsyncSession) -> None:
        """A new payment is persisted and returned with correct fields."""
        order_id = uuid.uuid4()
        key = str(uuid.uuid4())

        payment = await repo.create_payment(
            db_session,
            order_id=order_id,
            idempotency_key=key,
            amount=Decimal("250.00"),
            items=_SAMPLE_ITEMS,
        )

        assert payment.id is not None
        assert payment.order_id == order_id
        assert payment.idempotency_key == key
        assert payment.amount == Decimal("250.00")
        assert payment.items == _SAMPLE_ITEMS
        assert payment.status == PaymentStatus.PENDING

    async def test_create_sets_pending_status(self, db_session: AsyncSession) -> None:
        """Newly created payments always start as PENDING."""
        payment = await repo.create_payment(
            db_session,
            order_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            amount=Decimal("100.00"),
            items=_SAMPLE_ITEMS,
        )
        assert payment.status == PaymentStatus.PENDING


class TestGetByIdempotencyKey:
    """Tests for ``get_by_idempotency_key``."""

    async def test_returns_existing_payment(self, db_session: AsyncSession) -> None:
        key = str(uuid.uuid4())
        created = await repo.create_payment(
            db_session,
            order_id=uuid.uuid4(),
            idempotency_key=key,
            amount=Decimal("50.00"),
            items=_SAMPLE_ITEMS,
        )

        found = await repo.get_by_idempotency_key(db_session, key)
        assert found is not None
        assert found.id == created.id

    async def test_returns_none_when_not_found(self, db_session: AsyncSession) -> None:
        result = await repo.get_by_idempotency_key(db_session, "nonexistent-key")
        assert result is None


class TestGetByOrderId:
    """Tests for ``get_by_order_id``."""

    async def test_returns_payment_for_order(self, db_session: AsyncSession) -> None:
        order_id = uuid.uuid4()
        created = await repo.create_payment(
            db_session,
            order_id=order_id,
            idempotency_key=str(uuid.uuid4()),
            amount=Decimal("75.00"),
            items=_SAMPLE_ITEMS,
        )

        found = await repo.get_by_order_id(db_session, order_id)
        assert found is not None
        assert found.id == created.id

    async def test_returns_none_when_order_not_found(
        self, db_session: AsyncSession
    ) -> None:
        result = await repo.get_by_order_id(db_session, uuid.uuid4())
        assert result is None


class TestGetByMpPreferenceId:
    """Tests for ``get_by_mp_preference_id``."""

    async def test_returns_payment_by_preference(self, db_session: AsyncSession) -> None:
        payment = await repo.create_payment(
            db_session,
            order_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            amount=Decimal("200.00"),
            items=_SAMPLE_ITEMS,
        )
        await repo.update_with_preference(
            db_session,
            payment,
            mp_preference_id="PREF-123",
            init_point="https://mp.com/pay",
        )

        found = await repo.get_by_mp_preference_id(db_session, "PREF-123")
        assert found is not None
        assert found.id == payment.id

    async def test_returns_none_when_not_found(self, db_session: AsyncSession) -> None:
        result = await repo.get_by_mp_preference_id(db_session, "PREF-GHOST")
        assert result is None


class TestUpdateWithPreference:
    """Tests for ``update_with_preference``."""

    async def test_stores_preference_data(self, db_session: AsyncSession) -> None:
        payment = await repo.create_payment(
            db_session,
            order_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            amount=Decimal("300.00"),
            items=_SAMPLE_ITEMS,
        )

        updated = await repo.update_with_preference(
            db_session,
            payment,
            mp_preference_id="PREF-456",
            init_point="https://mp.com/checkout/456",
        )

        assert updated.mp_preference_id == "PREF-456"
        assert updated.init_point == "https://mp.com/checkout/456"


class TestUpdateStatus:
    """Tests for ``update_status``."""

    async def test_updates_to_approved(self, db_session: AsyncSession) -> None:
        payment = await repo.create_payment(
            db_session,
            order_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            amount=Decimal("150.00"),
            items=_SAMPLE_ITEMS,
        )

        updated = await repo.update_status(
            db_session,
            payment,
            status=PaymentStatus.APPROVED,
            mp_payment_id="MP-PAY-789",
        )

        assert updated.status == PaymentStatus.APPROVED
        assert updated.mp_payment_id == "MP-PAY-789"

    async def test_updates_to_rejected_without_mp_payment_id(
        self, db_session: AsyncSession
    ) -> None:
        payment = await repo.create_payment(
            db_session,
            order_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            amount=Decimal("99.99"),
            items=_SAMPLE_ITEMS,
        )

        updated = await repo.update_status(
            db_session,
            payment,
            status=PaymentStatus.REJECTED,
        )

        assert updated.status == PaymentStatus.REJECTED
        assert updated.mp_payment_id is None

    async def test_status_persists_after_refresh(self, db_session: AsyncSession) -> None:
        """The status change must actually be persisted in PostgreSQL."""
        payment = await repo.create_payment(
            db_session,
            order_id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            amount=Decimal("500.00"),
            items=_SAMPLE_ITEMS,
        )

        await repo.update_status(
            db_session,
            payment,
            status=PaymentStatus.APPROVED,
            mp_payment_id="MP-PERSIST",
        )

        # Force a fresh read from the DB.
        key = payment.idempotency_key
        db_session.expire_all()
        refreshed = await repo.get_by_idempotency_key(
            db_session, key
        )
        assert refreshed is not None
        assert refreshed.status == PaymentStatus.APPROVED
        assert refreshed.mp_payment_id == "MP-PERSIST"
