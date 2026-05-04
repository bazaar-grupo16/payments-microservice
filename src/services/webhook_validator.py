"""
Webhook signature validation for MercadoPago notifications.

MercadoPago sends an ``x-signature`` header containing a timestamp
(``ts``) and a hash (``v1``), along with an ``x-request-id`` header.
We validate the HMAC-SHA256 signature to ensure the request is authentic.

See: https://www.mercadopago.com.ar/developers/es/docs/your-integrations/notifications/webhooks
"""

import hashlib
import hmac
import logging

logger = logging.getLogger(__name__)


def validate_webhook_signature(
    *,
    x_signature: str,
    x_request_id: str,
    data_id: str,
    secret: str,
) -> bool:
    """Validate an incoming MercadoPago webhook signature.

    Parameters
    ----------
    x_signature:
        Value of the ``x-signature`` header. Format: ``ts=...,v1=...``
    x_request_id:
        Value of the ``x-request-id`` header.
    data_id:
        The ``data.id`` value from the query string or body.
    secret:
        The webhook secret configured in MercadoPago.

    Returns
    -------
    bool
        ``True`` if the signature is valid, ``False`` otherwise.
    """
    try:
        parts = dict(
            part.split("=", 1) for part in x_signature.split(",")
        )
        ts = parts.get("ts", "")
        v1 = parts.get("v1", "")
    except (ValueError, AttributeError):
        logger.warning("Malformed x-signature header: %s", x_signature)
        return False

    if not ts or not v1:
        logger.warning("Missing ts or v1 in x-signature: %s", x_signature)
        return False

    # Build the manifest string as documented by MercadoPago.
    manifest = f"id:{data_id};request-id:{x_request_id};ts:{ts};"

    computed = hmac.new(
        secret.encode("utf-8"),
        manifest.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(computed, v1)
