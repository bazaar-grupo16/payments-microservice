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

from src.exceptions import (
    MercadoPagoError,
    PaymentItemAlreadyRefundedError,
    PaymentItemNotFoundError,
    PaymentNotFoundError,
    PaymentNotRefundableError,
)
from src.repositories import payment_repository as repo
from src.schemas.payment import (
    PaymentStatusResponse,
    PreferenceResponse,
)
from src.services import mercadopago_client as mp_client
from src.services.webhook_validator import validate_webhook_signature
from src.core.config import get_settings
from src.models.payment import Payment, PaymentStatus
from src.repositories import outbox_repository
from src.services.outbox_worker import process_outbox_event_inline

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
    # Only map final states. Non-final statuses must be ignored.
    if mp_status == "approved":
        return PaymentStatus.APPROVED
    if mp_status in ("rejected", "cancelled", "canceled"):
        return PaymentStatus.REJECTED
    if mp_status in ("refunded", "charged_back"):
        return PaymentStatus.REFUNDED
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

        # Note: we return the previously stored init_point without calling MP again.
        # If payment has been cancelled, the preference should not be returned
        if existing.status == PaymentStatus.CANCELED:
            logger.info(
                "Payment for idempotency key=%s is canceled — not returning preference",
                idempotency_key,
            )
            raise PaymentNotRefundableError(
                "Payment was canceled and cannot be retried."
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

    if "partially_refunded" in str(payload.get("status_detail", "")).lower():
        _confirm_refunded_items(db, payment)
        

    if payment.status != new_status:
        payment.status = new_status
        payment.mp_payment_id = str(data_id)

        event = outbox_repository.insert(
            db,
            event_type="NOTIFY_PAYMENT_RESULT",
            payload={
                "order_id": str(order_id),
                "status": new_status.value,
                "transaction_id": str(data_id),
            },
        )

        repo.update_status(
            db,
            payment,
            status=new_status,
            mp_payment_id=str(data_id),
        )

        await process_outbox_event_inline(db, event)


async def _confirm_refunded_items(
    db: Session,
    payment: Payment
) -> None:
    """Mark items as refunded in the DB and propagate to orders service
    when we receive a partially_refunded status."""
    payment_id = payment.id
    items = payment.items or []
    
    for item in items:
        if item.get("refunded") is True:
            refund_id = item.get("refund_id", None)
            
            if refund_id is None:
                continue
            
            status = await mp_client.get_refund_status(
                payment_id=str(payment_id),
                refund_id=refund_id,
            )
            
            if status != "approved":
                continue
            
            order_item_id = item.get("line_item_id")
            if order_item_id:
                event = outbox_repository.insert(
                    db,
                    event_type="NOTIFY_REFUND_RESULT",
                    payload={
                        "order_id": str(payment.order_id),
                        "order_item_id": order_item_id,
                        "refund_status": "approved",
                    },
                )
                db.commit()
                await process_outbox_event_inline(db, event)
            


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
        payment.status = new_status
        payment.mp_payment_id = transaction_id

        event = outbox_repository.insert(
            db,
            event_type="NOTIFY_PAYMENT_RESULT",
            payload={
                "order_id": str(operation_id),
                "status": new_status.value,
                "transaction_id": transaction_id,
            },
        )

        repo.update_status(
            db,
            payment,
            status=new_status,
            mp_payment_id=transaction_id,
        )

        await process_outbox_event_inline(db, event)


async def request_refund(
    db: Session,
    *,
    order_id: UUID,
    order_item_id: str,
) -> dict:
    """Request a refund for a specific item within a payment.

    Parameters
    ----------
    db:
        Active SQLAlchemy session.
    order_id:
        Order UUID whose payment will be partially refunded.
    order_item_id:
        The ``line_item_id`` of the specific item to refund.

    Returns
    -------
    dict
        Contains information about the refund: refund_id, order_id,
        order_item_id, status, and amount.

    Raises
    ------
    PaymentNotFoundError
        If no payment record exists for the given order.
    PaymentNotRefundableError
        If the payment is not in APPROVED status.
    PaymentItemNotFoundError
        If the item is not found within the payment.
    PaymentItemAlreadyRefundedError
        If the item has already been refunded.
    MercadoPagoError
        If the MercadoPago API call fails.
    """
    # 1. Find the payment
    payment = repo.get_by_order_id(db, order_id)
    if not payment:
        logger.warning("No payment found for order_id=%s", order_id)
        raise PaymentNotFoundError(f"No payment found for order {order_id}")

    # 2. Validate that the payment can be refunded
    if payment.status != PaymentStatus.APPROVED:
        logger.info(
            "Payment %s is in status %s — not refundable",
            payment.id,
            payment.status.value,
        )
        raise PaymentNotRefundableError(
            f"Payment is in status '{payment.status.value}' and cannot be refunded. "
            "Only APPROVED payments can be refunded."
        )

    # 3. Find the specific item within the payment
    items = payment.items or []
    target_item = next(
        (item for item in items if item.get("line_item_id") == order_item_id),
        None,
    )
    if target_item is None:
        logger.warning(
            "Item %s not found in payment %s",
            order_item_id,
            payment.id,
        )
        raise PaymentItemNotFoundError(
            f"Item '{order_item_id}' not found in payment for order {order_id}"
        )

    # 4. Check if the item was already refunded
    if target_item.get("refunded") is True:
        logger.info(
            "Item %s in payment %s was already refunded",
            order_item_id,
            payment.id,
        )
        raise PaymentItemAlreadyRefundedError(
            f"Item '{order_item_id}' has already been refunded"
        )

    # 5. Ensure the payment has a MercadoPago payment ID
    if not payment.mp_payment_id:
        logger.error(
            "Cannot refund payment %s — no mp_payment_id",
            payment.id,
        )
        raise MercadoPagoError("Payment has no MercadoPago payment ID")

    # 6. Calculate the amount for this item
    amount = float(
        Decimal(str(target_item["quantity"])) * Decimal(str(target_item["unit_price"]))
    )

    # 7. Build idempotency key and call MercadoPago
    idempotency_key = f"{order_id}-{order_item_id}"
    try:
        refund_data = await mp_client.create_refund(
            payment.mp_payment_id,
            amount=amount,
            idempotency_key=idempotency_key,
        )
    except httpx.HTTPStatusError as exc:
        logger.error(
            "MercadoPago refund API error for payment %s: %s",
            payment.id,
            exc,
        )
        raise MercadoPagoError(
            f"MercadoPago refund failed: {exc.response.status_code}"
        ) from exc

    # 8. Mark the item as refunded in the DB
    payment = repo.mark_item_as_refunded(db, payment, order_item_id, refund_data.get("id", None))

    # 9. If all items are now refunded, mark the full payment as REFUNDED
    all_refunded = all(item.get("refunded") is True for item in (payment.items or []))
    if all_refunded:
        repo.update_status(
            db,
            payment,
            status=PaymentStatus.REFUNDED,
            mp_payment_id=payment.mp_payment_id,
        )
        logger.info(
            "All items refunded — payment %s marked as REFUNDED",
            payment.id,
        )

    logger.info(
        "Refund requested for item %s in payment %s — refund_id=%s",
        order_item_id,
        payment.id,
        refund_data.get("id"),
    )

    return {
        "refund_id": str(refund_data.get("id")),
        "order_id": str(order_id),
        "order_item_id": order_item_id,
        "status": refund_data.get("status", "pending"),
        "amount": refund_data.get("amount"),
    }


async def cancel_payment(
    db: Session,
    *,
    order_id: UUID,
) -> dict:
    """Cancel a pending payment.

    Parameters
    ----------
    db:
        Active SQLAlchemy session.
    order_id:
        Order UUID whose payment will be canceled. Only payments in PENDING status can be canceled.
    Returns
    -------
    dict
        Contains information about the cancellation: order_id and new payment status.
    Raises
    ------
    PaymentNotFoundError
        If no payment record exists for the given order.
    PaymentNotRefundableError
        If the payment is not in PENDING status and therefore cannot be canceled.
    MercadoPagoError
        If the MercadoPago API call fails during cancellation.
    """
    # 1. Find the payment
    payment = repo.get_by_order_id(db, order_id)
    if not payment:
        logger.warning("No payment found for order_id=%s", order_id)
        raise PaymentNotFoundError(f"No payment found for order {order_id}")

    # 2. Validate that the payment can be canceled
    if payment.status != PaymentStatus.PENDING:
        logger.info(
            "Payment %s is in status %s — cannot be canceled",
            payment.id,
            payment.status.value,
        )
        raise PaymentNotRefundableError(
            f"Payment is in status '{payment.status.value}' and cannot be canceled. "
            "Only PENDING payments can be canceled."
        )
    
    # 3. Update payment status to CANCELED
    repo.update_status(
        db,
        payment,
        status=PaymentStatus.CANCELED,
    )

    logger.info(
        "Payment %s for order %s has been canceled",
        payment.id,
        order_id,
    )

    return {
        "order_id": str(order_id),
        "payment_status": PaymentStatus.CANCELED.value,
    }