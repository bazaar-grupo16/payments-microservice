"""Integration tests for observability behavior (correlation/request IDs)."""

from __future__ import annotations

from unittest.mock import patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, TraceState

from src.core import observability


def product_form(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "title": "Mechanical Keyboard",
        "description": "Compact keyboard with tactile switches",
        "price": "129.90",
        "stock": "8",
        "category": "Peripherals",
    }
    data.update({key: str(value) for key, value in overrides.items()})
    return data


class TestObservability:
    def test_uses_otel_trace_id_when_a_span_is_active(self, client: TestClient) -> None:
        trace_id = int("1234567890abcdef1234567890abcdef", 16)
        span_id = int("0123456789abcdef", 16)
        span_context = SpanContext(
            trace_id=trace_id,
            span_id=span_id,
            is_remote=False,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
            trace_state=TraceState(),
        )

        with patch(
            "src.core.observability.trace.get_current_span",
            return_value=NonRecordingSpan(span_context),
        ):
            response = client.get("/livez")

        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == "1234567890abcdef1234567890abcdef"
        assert response.headers["X-Correlation-ID"] == "1234567890abcdef1234567890abcdef"

    def test_falls_back_to_x_correlation_id_when_request_id_is_missing(
        self, client: TestClient
    ) -> None:
        correlation_id = str(uuid4())

        response = client.get("/livez", headers={"X-Correlation-ID": correlation_id})

        assert response.status_code == 200
        assert response.headers["X-Request-ID"] == correlation_id
        assert response.headers["X-Correlation-ID"] == correlation_id

    def test_generates_ids_when_no_header_is_provided(self, client: TestClient) -> None:
        response = client.get("/livez")

        assert response.status_code == 200
        generated_request_id = response.headers["X-Request-ID"]
        generated_correlation_id = response.headers["X-Correlation-ID"]
        assert generated_request_id == generated_correlation_id
        UUID(generated_request_id)

    def test_build_observability_headers_uses_current_request_id(self) -> None:
        token = observability.set_correlation_id("1234567890abcdef1234567890abcdef")
        try:
            headers = observability.build_observability_headers()
        finally:
            observability.reset_correlation_id(token)

        assert headers == {
            "X-Request-ID": "1234567890abcdef1234567890abcdef",
            "X-Correlation-ID": "1234567890abcdef1234567890abcdef",
        }


class TestMetricsEndpoint:
    def test_returns_prometheus_text(self, client: TestClient) -> None:
        response = client.get("/metrics")

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
        assert response.text