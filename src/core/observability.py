"""Observability primitives for correlation-aware structured logging."""

from __future__ import annotations

import contextvars
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from fastapi import Request, Response
from opentelemetry import trace
from pythonjsonlogger import jsonlogger
from starlette.middleware.base import BaseHTTPMiddleware


correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id",
    default=None,
)

REQUEST_ID_HEADER = "X-Request-ID"
CORRELATION_ID_HEADER = "X-Correlation-ID"


def get_correlation_id() -> str | None:
    """Return the correlation id stored in the current context."""
    return correlation_id_var.get()


def set_correlation_id(correlation_id: str | None) -> contextvars.Token[str | None]:
    """Set the current correlation id and return a reset token."""
    return correlation_id_var.set(correlation_id)


def reset_correlation_id(token: contextvars.Token[str | None]) -> None:
    """Reset the correlation id context variable to a previous state."""
    correlation_id_var.reset(token)


def resolve_correlation_id(headers: Mapping[str, str]) -> str:
    """Get correlation id from the current OTel span or inbound headers."""
    span = trace.get_current_span()
    span_context = span.get_span_context()
    if span_context.is_valid:
        return format(span_context.trace_id, "032x")

    request_id = headers.get(REQUEST_ID_HEADER)
    if request_id:
        request_id = request_id.strip()
        if request_id:
            return request_id

    correlation_id = headers.get(CORRELATION_ID_HEADER)
    if correlation_id:
        correlation_id = correlation_id.strip()
        if correlation_id:
            return correlation_id

    return uuid4().hex


def _current_span_ids() -> tuple[str | None, str | None]:
    """Return the current OTel trace/span ids, if a valid span exists."""
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None, None

    return format(span_context.trace_id, "032x"), format(span_context.span_id, "016x")


def get_observability_correlation_id() -> str | None:
    """Return the correlation id for the current request or span."""
    correlation_id = get_correlation_id()
    if correlation_id:
        return correlation_id

    trace_id, _ = _current_span_ids()
    return trace_id


def build_observability_headers() -> dict[str, str]:
    """Return outbound headers with correlation ids when available."""
    correlation_id = get_observability_correlation_id()
    if not correlation_id:
        return {}

    return {
        REQUEST_ID_HEADER: correlation_id,
        CORRELATION_ID_HEADER: correlation_id,
    }


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Propagate correlation ids across request context and response headers."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        correlation_id = resolve_correlation_id(request.headers)
        token = set_correlation_id(correlation_id)

        try:
            response = await call_next(request)
        finally:
            reset_correlation_id(token)

        response.headers[REQUEST_ID_HEADER] = correlation_id
        response.headers[CORRELATION_ID_HEADER] = correlation_id
        return response


class ObservabilityJSONFormatter(jsonlogger.JsonFormatter):
    """JSON formatter that injects correlation and OTel fields."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S.%LZ",
            rename_fields={
                "asctime": "time",
                "levelname": "level",
                "name": "logger",
            },
        )

    def add_fields(
        self,
        log_record: dict[str, Any],
        record: Any,
        message_dict: dict[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)

        correlation_id = get_observability_correlation_id()
        trace_id = getattr(record, "trace_id", None)
        span_id = getattr(record, "span_id", None)

        if trace_id is None or span_id is None:
            current_trace_id, current_span_id = _current_span_ids()
            trace_id = trace_id or current_trace_id
            span_id = span_id or current_span_id

        log_record["correlation_id"] = correlation_id
        log_record["trace_id"] = trace_id
        log_record["span_id"] = span_id