"""
HTTP client for the Orders microservice (internal webhook).

Notifies the Orders service about payment results as part of the
Saga orchestration.  Implements retry with exponential backoff
for transient failures.
"""

import logging
import asyncio
import httpx

from src.core.config import get_settings
from src.core.observability import build_observability_headers

logger = logging.getLogger(__name__)

# ── Retry configuration ──────────────────────────────
_MAX_RETRIES = 3
_INITIAL_BACKOFF = 0.5  # seconds
_BACKOFF_FACTOR = 2.0


async def notify_payment_result(
    order_id: str,
    status: str,
    transaction_id: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> bool:
    """Send the payment result to the Orders microservice.

    Parameters
    ----------
    order_id:
        UUID of the order as a string.
    status:
        ``"APPROVED"`` or ``"REJECTED"``.
    transaction_id:
        The MercadoPago payment ID or transaction ID.
    http_client:
        Optional injected client for testing.

    Returns
    -------
    bool
        ``True`` if notification was accepted, ``False`` on permanent
        failure after all retries.
    """
    settings = get_settings()
    url = (
        f"{settings.ORDERS_SERVICE_URL}/orders/"
        f"{order_id}/payment-webhook"
    )
    payload = {
        "status": status,
        "transaction_id": transaction_id,
    }

    client = http_client or httpx.AsyncClient()
    backoff = _INITIAL_BACKOFF

    try:
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = await client.post(
                    url,
                    json=payload,
                    headers=build_observability_headers(),
                    timeout=10.0,
                )
                response.raise_for_status()
                logger.info(
                    "Notified Orders service: order=%s status=%s "
                    "(attempt %d)",
                    order_id,
                    status,
                    attempt,
                )
                return True

            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                logger.warning(
                    "Orders notification failed (attempt %d/%d): %s",
                    attempt,
                    _MAX_RETRIES,
                    exc,
                )
                if attempt == _MAX_RETRIES:
                    logger.error(
                        "Orders notification exhausted retries for "
                        "order=%s status=%s",
                        order_id,
                        status,
                    )
                    return False

                await asyncio.sleep(backoff)
                backoff *= _BACKOFF_FACTOR
    finally:
        if http_client is None:
            await client.aclose()

    return False  # pragma: no cover

async def notify_refund_result(
    order_id: str,
    order_item_id: str,
    refund_status: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> bool:
    """Notify the Orders service about a refund result.

    Parameters
    ----------
    order_id:
        UUID of the order as a string.
    order_item_id:
        The line_item_id of the specific item being refunded.
    refund_status:
        The status of the refund, e.g. "approved", "rejected", etc.
        
    http_client:
        Optional injected client for testing.

    Returns
    -------
    bool
        ``True`` if notification was accepted, ``False`` on permanent
        failure after all retries.
    """
    settings = get_settings()
    url = (
        f"{settings.ORDERS_SERVICE_URL}/orders/"
        f"{order_id}/refund-webhook"
    )
    payload = {
        "order_item_id": order_item_id,
        "refund_status": refund_status,
    }

    client = http_client or httpx.AsyncClient()
    backoff = _INITIAL_BACKOFF

    try:
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = await client.post(
                    url,
                    json=payload,
                    headers=build_observability_headers(),
                    timeout=10.0,
                )
                response.raise_for_status()
                logger.info(
                    "Notified Orders service about refund: order=%s "
                    "item=%s status=%s (attempt %d)",
                    order_id,
                    order_item_id,
                    refund_status,
                    attempt,
                )
                return True
            except (httpx.HTTPStatusError, httpx.TransportError) as exc:
                logger.warning(
                    "Refund notification failed (attempt %d/%d): %s",
                    attempt,
                    _MAX_RETRIES,
                    exc,
                )
                if attempt == _MAX_RETRIES:
                    logger.error(
                        "Refund notification exhausted retries for "
                        "order=%s item=%s status=%s",
                        order_id,
                        order_item_id,
                        refund_status,
                    )
                    return False

                await asyncio.sleep(backoff)
                backoff *= _BACKOFF_FACTOR
    finally:
        if http_client is None:
            await client.aclose()
    
    return False  # pragma: no cover