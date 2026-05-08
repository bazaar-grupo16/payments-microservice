"""
Payment API router.

Defines the HTTP endpoints for the Payments microservice.
All routes are mounted under ``/payments``.
"""

import logging
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, status

from src.core.dependencies import DbSession
from src.exceptions import MercadoPagoError, PaymentNotFoundError
from src.schemas.payment import (
    ErrorResponse,
    PaymentStatusResponse,
    PreferenceRequest,
    PreferenceResponse,
    DebugPaymentRequest,
)
from src.services import payment_service

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/payments",
    tags=["Payments"],
)


@router.post(
    "/preferences",
    status_code=status.HTTP_201_CREATED,
    response_model=PreferenceResponse,
    responses={
        400: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
    summary="Crea una preferencia de pago en MercadoPago.",
    description=(
        "Se comunica con la API de MP, obtiene el init_point y lo "
        "devuelve. Valida idempotencia localmente."
    ),
)
async def create_preference(
    body: PreferenceRequest,
    db: DbSession,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        description="Clave única para evitar preferencias duplicadas.",
    ),
) -> PreferenceResponse:
    """Create a MercadoPago Checkout Pro preference."""
    try:
        items_dicts = [item.model_dump() for item in body.items]
        return await payment_service.create_preference(
            db,
            idempotency_key=idempotency_key,
            order_id=body.order_id,
            items=items_dicts,
        )
    except MercadoPagoError as exc:
        logger.error("MercadoPago error: %s", exc.detail)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=exc.detail,
        ) from exc


@router.get(
    "/{order_id}/status",
    response_model=PaymentStatusResponse,
    responses={404: {"model": ErrorResponse}},
    summary="Consulta el estado interno del pago asociado a una orden.",
    description=(
        "Utilizado por el frontend para hacer polling tras "
        "volver del deep link."
    ),
)
async def get_payment_status(
    order_id: UUID,
    db: DbSession,
) -> PaymentStatusResponse:
    """Return the current payment status for a given order."""
    try:
        return await payment_service.get_payment_status(db, order_id)
    except PaymentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.detail,
        ) from exc


@router.post(
    "/mp-webhook",
    status_code=status.HTTP_200_OK,
    summary="Recibe notificaciones IPN/Webhooks desde MercadoPago.",
    description=(
        "Endpoint público consumido por MercadoPago. Valida la firma del request, "
        "actualiza la base de datos local y dispara el Webhook interno hacia el MS de Órdenes."
    ),
)
async def mp_webhook(
    payload: dict,
    request: Request,
    db: DbSession,
    x_signature: str | None = Header(None, alias="x-signature"),
    x_request_id: str | None = Header(None, alias="x-request-id"),
) -> dict:
    """Process a MercadoPago webhook."""
    data = payload.get("data", {})
    query_data_id = request.query_params.get("data.id")
    if query_data_id:
        payload = {**payload, "data": {**data, "id": query_data_id}}
    elif not data.get("id") and payload.get("id"):
        payload = {**payload, "data": {**data, "id": payload["id"]}}

    try:
        await payment_service.process_webhook(
            db=db,
            payload=payload,
            x_signature=x_signature,
            x_request_id=x_request_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return {"status": "ok"}


@router.post(
    "/debug",
    status_code=status.HTTP_200_OK,
    summary="Endpoint de debug para validar un pago manualmente.",
    description="Simula un webhook para aprobar o rechazar un pago.",
)
async def debug_webhook(
    body: DebugPaymentRequest,
    db: DbSession,
) -> dict:
    """Manual debug webhook to resolve a payment."""
    try:
        await payment_service.process_debug_webhook(
            db=db,
            operation_id=body.operation_id,
            accepted=body.accepted,
        )
    except PaymentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.detail,
        ) from exc
    return {"status": "ok", "operation_id": body.operation_id, "accepted": body.accepted}

