"""
HTTP client for MercadoPago Checkout Pro.

Wraps the MercadoPago REST API using ``httpx`` for full control over
HTTP calls and easy mocking in tests.  The official SDK is not used
because raw ``httpx`` is simpler to inject and mock with ``pytest-httpx``.
"""

import logging
from dataclasses import dataclass

import httpx

from src.core.config import get_settings

logger = logging.getLogger(__name__)

_MP_BASE_URL = "https://api.mercadopago.com"


@dataclass(frozen=True)
class PreferenceResult:
    """Data returned after creating a MercadoPago preference."""

    preference_id: str
    init_point: str


async def create_preference(
    *,
    items: list[dict],
    external_reference: str,
    notification_url: str | None = None,
    back_urls: dict | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> PreferenceResult:
    """Create a Checkout Pro preference via the MercadoPago API.

    Parameters
    ----------
    items:
        List of item dicts, each with ``title``, ``quantity``, and
        ``unit_price``.  Sent directly to the MP Preferences API.
    external_reference:
        Our ``order_id`` so MP can reference it back in webhooks.
    notification_url:
        Webhook URL for MP notifications. If ``None``, no webhook is configured.
    back_urls:
        Dict with "success", "pending", and "failure" URLs for post-payment redirect.
    http_client:
        Optional injected client (used in tests). If ``None``, a new
        ``httpx.AsyncClient`` is created for this call.

    Returns
    -------
    PreferenceResult
        Contains ``preference_id`` and ``init_point`` (checkout URL).

    Raises
    ------
    httpx.HTTPStatusError
        If MercadoPago returns a non-2xx status.
    """
    settings = get_settings()
    headers = {
        "Authorization": f"Bearer {settings.MP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "items": items,
        "external_reference": external_reference,
    }
    
    if notification_url:
        payload["notification_url"] = notification_url
    
    if back_urls:
        payload["back_urls"] = back_urls

    client = http_client or httpx.AsyncClient()
    try:
        response = await client.post(
            f"{_MP_BASE_URL}/checkout/preferences",
            json=payload,
            headers=headers,
            timeout=10.0,
        )
        response.raise_for_status()
    finally:
        if http_client is None:
            await client.aclose()

    data = response.json()
    logger.info(
        "Created MP preference %s for external_ref=%s",
        data["id"],
        external_reference,
    )
    return PreferenceResult(
        preference_id=data["id"],
        init_point=data["init_point"],
    )


async def get_payment_info(
    payment_id: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> dict:
    """Fetch payment details from MercadoPago by payment ID.

    Parameters
    ----------
    payment_id:
        The MercadoPago payment ID received in the webhook.
    http_client:
        Optional injected client for testing.

    Returns
    -------
    dict
        Full payment object from MercadoPago.

    Raises
    ------
    httpx.HTTPStatusError
        If MercadoPago returns a non-2xx status.
    """
    settings = get_settings()
    headers = {"Authorization": f"Bearer {settings.MP_ACCESS_TOKEN}"}

    client = http_client or httpx.AsyncClient()
    try:
        response = await client.get(
            f"{_MP_BASE_URL}/v1/payments/{payment_id}",
            headers=headers,
            timeout=10.0,
        )
        response.raise_for_status()
    finally:
        if http_client is None:
            await client.aclose()

    data = response.json()
    logger.info(
        "Fetched MP payment %s — status=%s",
        payment_id,
        data.get("status"),
    )
    return data


async def create_refund(
    payment_id: str,
    *,
    amount: float | None = None,
    idempotency_key: str,
    http_client: httpx.AsyncClient | None = None,
) -> dict:
    """Request a refund for a payment from MercadoPago.

    Parameters
    ----------
    payment_id:
        The MercadoPago payment ID to refund.
    amount:
        Optional refund amount. If ``None``, full refund is requested.
    idempotency_key:
        Unique key to guarantee idempotency of the refund request.
        Should be built by the caller as ``{order_id}-{order_item_id}``.
    http_client:
        Optional injected client for testing.

    Returns
    -------
    dict
        Refund object from MercadoPago with keys like ``id``,
        ``payment_id``, ``amount``, ``status``, etc.

    Raises
    ------
    httpx.HTTPStatusError
        If MercadoPago returns a non-2xx status.
    """
    settings = get_settings()
    headers = {
        "Authorization": f"Bearer {settings.MP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "X-Idempotency-Key": idempotency_key,
    }
    
    payload = {}
    if amount is not None:
        payload["amount"] = amount

    client = http_client or httpx.AsyncClient()
    try:
        response = await client.post(
            f"{_MP_BASE_URL}/v1/payments/{payment_id}/refunds",
            json=payload,
            headers=headers,
            timeout=10.0,
        )
        response.raise_for_status()
    finally:
        if http_client is None:
            await client.aclose()

    data = response.json()
    logger.info(
        "Created refund %s for MP payment %s — status=%s, amount=%s",
        data.get("id"),
        payment_id,
        data.get("status"),
        data.get("amount"),
    )
    return data

async def get_refund_status(
    payment_id: str,
    refund_id: str,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """Fetch refund details from MercadoPago by payment ID and refund ID.

    Parameters
    ----------
    payment_id:
        The MercadoPago payment ID associated with the refund.
    refund_id:
        The MercadoPago refund ID to fetch.
    http_client:
        Optional injected client for testing.

    Returns
    -------
    str
        Refund status string from MercadoPago (or empty string if absent).

    Raises
    ------
    httpx.HTTPStatusError
        If MercadoPago returns a non-2xx status.
    """
    settings = get_settings()
    headers = {"Authorization": f"Bearer {settings.MP_ACCESS_TOKEN}"}

    client = http_client or httpx.AsyncClient()
    try:
        response = await client.get(
            f"{_MP_BASE_URL}/v1/payments/{payment_id}/refunds/{refund_id}",
            headers=headers,
            timeout=10.0,
        )
        response.raise_for_status()
    finally:
        if http_client is None:
            await client.aclose()

    data = response.json()
    status = data.get("status") or ""
    logger.info(
        "Fetched MP refund %s for payment %s — status=%s",
        refund_id,
        payment_id,
        status,
    )
    return status