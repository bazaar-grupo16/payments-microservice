"""
Pydantic schemas for the Payments API.

These schemas define the request/response contracts and MUST match
the OpenAPI specification in ``.agents/openapi.yaml``.
"""

from uuid import UUID

from pydantic import BaseModel, Field


# ── Item ─────────────────────────────────────────────────────


class Item(BaseModel):
    """A single item within a preference request."""

    title: str = Field(
        ..., description="Descripción del ítem a mostrar en MercadoPago."
    )
    quantity: int = Field(..., ge=1, json_schema_extra={"example": 1})
    unit_price: float = Field(
        ..., gt=0, description="Precio unitario del ítem."
    )


# ── Requests ─────────────────────────────────────────────────


class PreferenceRequest(BaseModel):
    """Body for ``POST /payments/preferences``."""

    order_id: UUID = Field(
        ..., description="ID de la orden generada por el orquestador."
    )
    items: list[Item] = Field(
        ...,
        description="Lista de ítems a cobrar en MercadoPago.",
    )


# ── Responses ────────────────────────────────────────────────


class PreferenceResponse(BaseModel):
    """Successful response for ``POST /payments/preferences``."""

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
    """Response for ``GET /payments/{order_id}/status``."""

    order_id: UUID
    payment_status: str = Field(
        ...,
        pattern="^(PENDING|APPROVED|REJECTED)$",
        description="Estado actual del pago.",
    )


class ErrorResponse(BaseModel):
    """Standard error body returned by the API."""

    detail: str
