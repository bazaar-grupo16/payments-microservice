"""
Pydantic schemas for the Payments API.

These schemas define the request/response contracts and MUST match
the OpenAPI specification in ``.agents/openapi.yaml``.
"""

from uuid import UUID

from pydantic import BaseModel, Field


# ── Requests ─────────────────────────────────────────────────


class PreferenceRequest(BaseModel):
    """Body for ``POST /api/v1/payments/preferences``."""

    order_id: UUID = Field(
        ..., description="ID de la orden generada por el orquestador."
    )
    title: str = Field(
        ..., description="Descripción de la compra a mostrar en MercadoPago."
    )
    quantity: int = Field(..., ge=1, example=1)
    unit_price: float = Field(
        ..., gt=0, description="Monto total a cobrar."
    )


# ── Responses ────────────────────────────────────────────────


class PreferenceResponse(BaseModel):
    """Successful response for ``POST /api/v1/payments/preferences``."""

    preference_id: str = Field(
        ..., description="ID interno de la preferencia de MercadoPago."
    )
    init_point: str = Field(
        ...,
        description=(
            "URL a la que el frontend debe redirigir al usuario "
            "para efectuar el pago."
        ),
    )


class PaymentStatusResponse(BaseModel):
    """Response for ``GET /api/v1/payments/{order_id}/status``."""

    order_id: UUID
    payment_status: str = Field(
        ...,
        pattern="^(PENDING|APPROVED|REJECTED)$",
        description="Estado actual del pago.",
    )


class ErrorResponse(BaseModel):
    """Standard error body returned by the API."""

    detail: str
