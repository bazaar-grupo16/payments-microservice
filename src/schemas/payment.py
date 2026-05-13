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
    line_item_id: str = Field(
        ..., description="ID único del ítem dentro de la orden."
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


class DebugPaymentRequest(BaseModel):
    """Body for the debug webhook endpoint."""

    operation_id: UUID = Field(
        ..., description="ID de la operación (orden) a simular."
    )
    accepted: bool = Field(
        ..., description="Si el pago fue aceptado o rechazado."
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


class RefundRequest(BaseModel):
    """Body for ``POST /payments/{order_id}/refund``."""

    order_item_id: str = Field(
        ...,
        description="ID del ítem de la orden a reembolsar (line_item_id del item).",
    )


class RefundResponse(BaseModel):
    """Response for a successful refund request."""

    refund_id: str = Field(
        ..., description="ID del reembolso en MercadoPago."
    )
    order_id: UUID = Field(
        ..., description="ID de la orden."
    )
    order_item_id: str = Field(
        ..., description="ID del ítem reembolsado."
    )
    status: str = Field(
        ..., description="Estado del reembolso."
    )
    amount: float = Field(
        ..., description="Monto reembolsado."
    )


class PaymentStatusResponse(BaseModel):
    """Response for ``GET /payments/{order_id}/status``."""

    order_id: UUID
    payment_status: str = Field(
        ...,
        pattern="^(PENDING|APPROVED|REJECTED|CANCELED|REFUNDED)$",
        description="Estado actual del pago.",
    )


class ErrorResponse(BaseModel):
    """Standard error body returned by the API."""

    detail: str
