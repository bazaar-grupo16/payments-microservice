"""
Payment service — business logic layer.

Orchestrates repository calls and external service clients.
All business rules (idempotency, amount calculation, etc.) live here.
"""

import logging
from decimal import Decimal
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from src.exceptions import MercadoPagoError, PaymentNotFoundError
from src.repositories import payment_repository as repo
from src.schemas.payment import (
    PaymentStatusResponse,
    PreferenceResponse,
)
from src.services import mercadopago_client as mp_client
from src.services import orders_client
from src.services.webhook_validator import validate_webhook_signature
from src.core.config import get_settings
from src.models.payment import PaymentStatus

logger = logging.getLogger(__name__)


def _compute_total(items: list[dict]) -> Decimal:
    """Sum quantity * unit_price for every item."""
    return sum(
        Decimal(str(item["quantity"])) * Decimal(str(item["unit_price"]))
        for item in items
    )


def _map_mp_status(mp_status: str | None) -> PaymentStatus | None:
    """Translate MercadoPago payment states into internal states.

    Only final states are propagated to the rest of the system.
    Non-final or unknown states are ignored to avoid regressing a
    payment back to ``PENDING`` after it has already been resolved.
    """
    if mp_status == "approved":
        return PaymentStatus.APPROVED
    if mp_status in ("rejected", "cancelled"):
        return PaymentStatus.REJECTED
    return None


async def create_preference(
    db: Session,
    *,
    idempotency_key: str,
    order_id: UUID,
    items: list[dict],
) -> PreferenceResponse:
    """Create a MercadoPago preference (idempotent).

    If the ``idempotency_key`` already exists in the database the
    previously stored ``init_point`` is returned **without** calling
    MercadoPago again.

    Parameters
    ----------
    db:
        Active SQLAlchemy session.
    idempotency_key:
        UUID from the ``Idempotency-Key`` request header.
    order_id:
        Order UUID from the request body.
    items:
        List of item dicts with title, quantity, unit_price.

    Returns
    -------
    PreferenceResponse
        Contains ``preference_id`` and ``init_point``.

    Raises
    ------
    MercadoPagoError
        If the upstream MercadoPago API call fails.
    """
    # ── Idempotency check ────────────────────────────
    existing = repo.get_by_idempotency_key(db, idempotency_key)
    if existing and existing.init_point:
        logger.info(
            "Idempotency hit for key=%s — returning cached preference",
            idempotency_key,
        )
        return PreferenceResponse(
            preference_id=existing.mp_preference_id,
            init_point=existing.init_point,
        )

    # ── Compute total amount ─────────────────────────
    amount = _compute_total(items)

    # ── Persist payment in PENDING status ────────────
    payment = repo.create_payment(
        db,
        order_id=order_id,
        idempotency_key=idempotency_key,
        amount=amount,
        items=items,
    )

    # ── Build notification and back URLs ────────────
    settings = get_settings()
    notification_url = f"{settings.WEBHOOK_BASE_URL}/payments/mp-webhook"
    back_urls = {
        "success": f"{settings.BAZAAR_BASE_URL}/{order_id}",
        "pending": f"{settings.BAZAAR_BASE_URL}/{order_id}",
        "failure": f"{settings.BAZAAR_BASE_URL}/{order_id}",
    }

    # ── Call MercadoPago ─────────────────────────────
    try:
        result = await mp_client.create_preference(
            items=items,
            external_reference=str(order_id),
            notification_url=notification_url,
            back_urls=back_urls,
        )
    except httpx.HTTPStatusError as exc:
        logger.error(
            "MercadoPago API error for order %s: %s", order_id, exc
        )
        raise MercadoPagoError(
            f"MercadoPago returned {exc.response.status_code}"
        ) from exc

    # ── Update payment with preference data ──────────
    repo.update_with_preference(
        db,
        payment,
        mp_preference_id=result.preference_id,
        init_point=result.init_point,
    )

    return PreferenceResponse(
        preference_id=result.preference_id,
        init_point=result.init_point,
    )


async def get_payment_status(
    db: Session,
    order_id: UUID,
) -> PaymentStatusResponse:
    """Look up the current payment status for an order.

    Raises
    ------
    PaymentNotFoundError
        If no payment record exists for the given order.
    """
    payment = repo.get_by_order_id(db, order_id)
    if payment is None:
        raise PaymentNotFoundError(
            f"No payment found for order {order_id}"
        )
    return PaymentStatusResponse(
        order_id=payment.order_id,
        payment_status=payment.status.value,
    )


async def process_webhook(
    db: Session,
    *,
    payload: dict,
    x_signature: str | None = None,
    x_request_id: str | None = None,
) -> None:
    """Process an incoming webhook from MercadoPago.

    Handles both 'payment' and 'merchant_order' webhook types.
    Validates the signature, fetches the payment info, updates the local DB,
    and notifies the Orders service.
    
    Note: Mercado Pago sends two types of webhooks:
    - payment: when a payment is created/updated (data.id is the payment_id)
    - merchant_order: when an order is updated (data may not contain payment_id)
    """
    # 1. Extract ID from payload (handles both payment and merchant_order types)
    data_id = payload.get("data", {}).get("id")
    # webhook_type = payload.get("type") or payload.get("topic")
    
    if not data_id:
        logger.warning(
            "Webhook payload missing ID — type=%s, topic=%s, payload keys=%s",
            payload.get("type"),
            payload.get("topic"),
            list(payload.keys()),
        )
        return

    logger.info(
        "Processing webhook — type=%s, topic=%s, id=%s",
        payload.get("type"),
        payload.get("topic"),
        data_id,
    )

    # 2. Validate signature
    settings = get_settings()
    if x_signature and x_request_id:
        is_valid = validate_webhook_signature(
            x_signature=x_signature,
            x_request_id=x_request_id,
            data_id=data_id,
            secret=settings.MP_WEBHOOK_SECRET,
        )
        if not is_valid:
            if settings.WEBHOOK_REQUIRE_SIGNATURE:
                logger.warning("Invalid webhook signature for id=%s", data_id)
                raise ValueError("Invalid webhook signature")
            else:
                logger.warning(
                    "Invalid webhook signature for id=%s (but WEBHOOK_REQUIRE_SIGNATURE=False, so continuing)",
                    data_id,
                )
    else:
        if settings.WEBHOOK_REQUIRE_SIGNATURE:
            logger.warning(
                "Webhook received without signature headers; failing because WEBHOOK_REQUIRE_SIGNATURE=True for id=%s",
                data_id,
            )
            raise ValueError("Webhook signature headers missing")
        else:
            logger.warning(
                "Webhook received without signature headers; continuing because WEBHOOK_REQUIRE_SIGNATURE=False for id=%s",
                data_id,
            )

    # 3. Fetch payment info from MP
    # The ID could be a payment_id (for 'payment' webhooks) or merchant_order_id (for 'merchant_order' webhooks)
    # Mercado Pago's webhook format uses the same ID field for both types, but represents different things
    try:
        mp_payment = await mp_client.get_payment_info(data_id)
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Failed to fetch payment info from MP for id=%s: %s",
            data_id,
            exc,
        )
        return

    mp_status = mp_payment.get("status")
    external_reference = mp_payment.get("external_reference")

    if not external_reference:
        logger.warning("Payment %s has no external_reference", data_id)
        return

    try:
        order_id = UUID(external_reference)
    except ValueError:
        logger.warning("Invalid external_reference format: %s", external_reference)
        return

    # 4. Update local DB
    payment = repo.get_by_order_id(db, order_id)
    if not payment:
        logger.warning("No payment found for order_id=%s", order_id)
        return

    new_status = _map_mp_status(mp_status)
    if new_status is None:
        logger.info(
            "Ignoring non-final MercadoPago status %r for order_id=%s",
            mp_status,
            order_id,
        )
        return

    if payment.status != new_status:
        repo.update_status(
            db,
            payment,
            status=new_status,
            mp_payment_id=str(data_id),
        )

        # 5. Notify Orders service
        notified = await orders_client.notify_payment_result(
            order_id=str(order_id),
            status=new_status.value,
            transaction_id=str(data_id),
        )
        if not notified:
            logger.warning(
                "Orders notification failed for order_id=%s transaction_id=%s",
                order_id,
                data_id,
            )


async def process_debug_webhook(
    db: Session,
    *,
    operation_id: UUID,
    accepted: bool,
) -> None:
    """Process a manual/debug webhook to simulate a payment resolution."""
    payment = repo.get_by_order_id(db, operation_id)
    if not payment:
        logger.warning("No payment found for operation_id=%s", operation_id)
        raise PaymentNotFoundError(f"No payment found for order {operation_id}")

    new_status = PaymentStatus.APPROVED if accepted else PaymentStatus.REJECTED
    transaction_id = "DEBUG-TX"

    if payment.status != new_status:
        repo.update_status(
            db,
            payment,
            status=new_status,
            mp_payment_id=transaction_id,
        )

        notified = await orders_client.notify_payment_result(
            order_id=str(operation_id),
            status=new_status.value,
            transaction_id=transaction_id,
        )
        if not notified:
            logger.warning(
                "Orders notification failed for operation_id=%s transaction_id=%s",
                operation_id,
                transaction_id,
            )
