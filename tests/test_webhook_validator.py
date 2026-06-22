"""
Tests for the webhook signature validator.
"""

import hmac
import hashlib
import logging
from src.services.webhook_validator import validate_webhook_signature


def test_validate_webhook_signature_success() -> None:
    # Added for coverage — validate_webhook_signature
    secret = "my_secret"
    data_id = "123456"
    request_id = "req-123"
    ts = "1620000000"
    
    manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
    v1 = hmac.new(
        secret.encode("utf-8"),
        manifest.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    
    x_signature = f"ts={ts},v1={v1}"
    
    result = validate_webhook_signature(
        x_signature=x_signature,
        x_request_id=request_id,
        data_id=data_id,
        secret=secret,
    )
    assert result is True


def test_validate_webhook_signature_malformed_signature() -> None:
    # Added for coverage — validate_webhook_signature
    result = validate_webhook_signature(
        x_signature="not-a-comma-separated-key-value",
        x_request_id="req-123",
        data_id="123456",
        secret="secret",
    )
    assert result is False


def test_validate_webhook_signature_missing_ts_or_v1() -> None:
    # Added for coverage — validate_webhook_signature
    # missing ts
    result1 = validate_webhook_signature(
        x_signature="v1=somehash",
        x_request_id="req-123",
        data_id="123456",
        secret="secret",
    )
    assert result1 is False

    # missing v1
    result2 = validate_webhook_signature(
        x_signature="ts=123",
        x_request_id="req-123",
        data_id="123456",
        secret="secret",
    )
    assert result2 is False


def test_validate_webhook_signature_invalid_match(caplog) -> None:
    # Added for coverage — validate_webhook_signature
    with caplog.at_level(logging.WARNING):
        result = validate_webhook_signature(
            x_signature="ts=123,v1=wronghash",
            x_request_id="req-123",
            data_id="123456",
            secret="secret",
        )
        assert result is False
        assert "Webhook signature validation failed" in caplog.text
