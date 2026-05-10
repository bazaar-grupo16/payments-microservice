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
        NOTE: This MUST be in lowercase per Mercado Pago documentation
        if it is alphanumeric.
    secret:
        The webhook secret configured in MercadoPago.

    Returns
    -------
    bool
        ``True`` if the signature is valid, ``False`` otherwise.
        
    References
    ----------
    https://www.mercadopago.com/developers/es/docs/your-integrations/notifications/webhooks
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

    # Per Mercado Pago documentation:
    # "Los parámetros con el sufijo `_url` provienen de _query params_.
    # Ejemplo: `[data.id_url]` se sustituirá por el valor correspondiente al ID del evento 
    # (`data.id`) y, en este caso, si el `data.id_url` es alfanumérico, 
    # deberá enviarse obligatoriamente en minúsculas."
    
    # Ensure data_id is lowercase if alphanumeric
    data_id_lower = str(data_id).lower() if data_id else data_id
    
    # Build the manifest string as documented by MercadoPago.
    # Format: id:[data.id];request-id:[x-request-id];ts:[ts];
    manifest = f"id:{data_id_lower};request-id:{x_request_id};ts:{ts};"

    computed = hmac.new(
        secret.encode("utf-8"),
        manifest.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    is_valid = hmac.compare_digest(computed, v1)
    
    if not is_valid:
        logger.warning(
            "Webhook signature validation failed — data_id=%s (lowercase: %s), request_id=%s, "
            "ts=%s, manifest=%s, computed_hash=%s, received_hash=%s",
            data_id,
            data_id_lower,
            x_request_id,
            ts,
            manifest,
            computed,
            v1,
        )
    else:
        logger.debug(
            "Webhook signature validation successful — data_id=%s, manifest=%s",
            data_id,
            manifest,
        )
    
    return is_valid
