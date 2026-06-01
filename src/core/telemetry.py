"""OpenTelemetry initialization for tracing and Prometheus metrics."""

from __future__ import annotations

import os
from functools import lru_cache

from fastapi import APIRouter, Response
from opentelemetry import metrics
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from prometheus_client import REGISTRY, generate_latest


router = APIRouter(tags=["Metrics"])


@router.get("/metrics", include_in_schema=False)
async def metrics_endpoint() -> Response:
    return Response(
        content=render_prometheus_metrics(),
        media_type="text/plain; version=0.0.4",
    )


def _get_deployment_environment() -> str:
    resource_attributes = os.getenv("OTEL_RESOURCE_ATTRIBUTES", "")
    for attribute in resource_attributes.split(","):
        key, separator, value = attribute.partition("=")
        if separator and key.strip() == "deployment.environment":
            cleaned_value = value.strip()
            if cleaned_value:
                return cleaned_value
    return "dev"


@lru_cache(maxsize=1)
def build_resource() -> Resource:
    """Build the shared OpenTelemetry resource from environment variables."""
    return Resource.create(
        {
            SERVICE_NAME: os.getenv("OTEL_SERVICE_NAME", "catalog"),
            "deployment.environment": _get_deployment_environment(),
        }
    )


@lru_cache(maxsize=1)
def initialize_prometheus_metrics() -> MeterProvider:
    """Configure the global meter provider with a Prometheus reader."""
    meter_provider = MeterProvider(
        resource=build_resource(),
        metric_readers=[PrometheusMetricReader()],
    )
    metrics.set_meter_provider(meter_provider)
    return meter_provider


def render_prometheus_metrics() -> bytes:
    """Render the default Prometheus registry in text format."""
    return generate_latest(REGISTRY)