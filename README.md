# 💳 Payments Microservice - Bazaar

Este microservicio se encarga de gestionar de forma centralizada la integración de pagos del e-commerce **Bazaar** con **MercadoPago Checkout Pro**.

---

## 🔄 Flujo de Integración (La Saga)

El proceso de compra en Bazaar sigue un patrón de Saga. A continuación se explican dos escenarios principales:

### 🟢 Happy Path (Flujo Exitoso)

1. **Creación de la Orden:** El usuario finaliza su carrito. El backend central crea la `Order` y solicita a Pagos la generación del link de cobro invocando `POST /payments/preferences`.
2. **Registro y Preferencia:**
   - El servicio de Pagos verifica que la `Idempotency-Key` no exista.
   - Crea un registro `Payment` en la base de datos local con status `PENDING`.
   - Llama a la API de MercadoPago (`POST /checkout/preferences`) para generar el link de pago (`init_point`).
   - Retorna el `init_point` al frontend (React Native).
3. **Pago del Usuario:** El usuario abre el `init_point`, completa los datos de su tarjeta y MercadoPago aprueba el pago.
4. **Notificación Webhook:** MercadoPago dispara un Webhook a `POST /payments/mp-webhook`.
   - Pagos verifica la firma criptográfica (HMAC-SHA256) usando `x-signature` y su `MP_WEBHOOK_SECRET`.
   - Pagos consulta a MercadoPago por el estado real del pago para evitar suplantaciones de identidad (vía `GET /v1/payments/{id}`).
   - Al confirmar el estado `APPROVED`, actualiza el `Payment` en su base de datos.
5. **Cierre de la Saga:** Pagos notifica al servicio de Órdenes (`POST /orders/{id}/payment-webhook`) para que este libere el pedido y comience la logística. El flujo culmina exitosamente.

### 🔴 Unhappy Path (Fallo en la Notificación a Órdenes)

El usuario completa el flujo y paga correctamente, pero **el servicio de Órdenes está caído** en el momento en que se recibe el webhook.

1. MP dispara el Webhook a `POST /mp-webhook`.
2. Pagos valida la firma, verifica que está `APPROVED` y actualiza su base de datos. El pago ya es válido localmente.
3. **Fallo:** Pagos intenta notificar a Órdenes mediante `POST /orders/{id}/payment-webhook` y recibe un error `503 Service Unavailable` o un *Timeout*.
4. **Resiliencia (Retry Backoff):** En lugar de fallar inmediatamente, el cliente interno de órdenes reintenta la petición:
   - Espera `0.5s` y reintenta. Si falla:
   - Espera `1.0s` y reintenta. Si falla:
   - Espera `2.0s` y reintenta.
5. Si tras 3 intentos el servicio de Órdenes sigue caído, el webhook finaliza retornando `200 OK` a MercadoPago (ya que el pago *sí* se procesó financieramente) pero la orden queda desincronizada temporalmente.

---

## 📦 Outbox Transaccional (Transactional Outbox)

Para resolver el problema del **Unhappy Path** y garantizar que las notificaciones al microservicio de Órdenes nunca se pierdan (incluso si está caído de forma prolongada), implementamos el patrón **Transactional Outbox**.

### ¿Cómo Funciona?

1. **Escritura Atómica:** Cuando ocurre un evento de pago o reembolso, en lugar de realizar una llamada HTTP directa a Órdenes dentro del flujo principal, insertamos un registro en la tabla `outbox_events` y actualizamos el estado de la entidad `Payment` **dentro de la misma transacción de base de datos**. Esto asegura consistencia eventual: si la transacción falla, nada se registra ni se encola.
2. **Envío Inmediato (Inline Dispatch):** Una vez confirmada (committed) la transacción, se intenta procesar el evento de inmediato mediante el despachador inline (`process_outbox_event_inline`):
   - Si el microservicio de Órdenes está disponible, el evento se entrega con éxito, se actualiza su estado a `DONE` y finaliza la petición con baja latencia.
   - Si el microservicio de Órdenes está caído, el error se captura silenciosamente para no interrumpir el flujo del cliente, y el evento permanece en estado `PENDING` para ser reprocesado más tarde. El webhook responde exitosamente (`200 OK`) a MercadoPago.
3. **Procesamiento en Segundo Plano (Worker):** Una tarea en segundo plano asíncrona (`run_outbox_worker`) se ejecuta periódicamente:
   - Consulta lotes de eventos en estado `PENDING` utilizando la sentencia `SELECT ... FOR UPDATE SKIP LOCKED`, lo cual permite escalar el microservicio horizontalmente con múltiples réplicas concurrentes sin colisiones ni bloqueos mutuos.
   - Despacha los eventos pendientes y, si fallan, incrementa el contador de intentos (`attempts`). Si un evento alcanza el límite `OUTBOX_MAX_ATTEMPTS` (por defecto 5), se marca permanentemente como `FAILED` para auditoría y alerta.

### ¿En qué casos se "activa"?

El outbox genera y despacha eventos en los siguientes escenarios:
1. **`NOTIFY_PAYMENT_RESULT`**: Se activa cuando el webhook de MercadoPago confirma que un pago fue aprobado (`APPROVED`) o rechazado (`REJECTED`) (ejecutado en `process_webhook` y `process_debug_webhook`).
2. **`NOTIFY_REFUND_RESULT`**: Se activa cuando se aprueba y confirma el reembolso de un ítem de orden (ejecutado en `_confirm_refunded_items`).

---

## 📂 Estructura del Proyecto

```text
.
├── alembic/                   # Migraciones de base de datos
│   └── versions/
│       └── 20260608000000_add_outbox_events_table.py  # Migración para la tabla de outbox
├── k8s/                       # Manifiestos y configuración de Kubernetes
│   ├── app-deployment.yaml
│   ├── app-service.yaml
│   ├── db-deployment.yaml
│   ├── db-pvc.yaml
│   ├── db-service.yaml
│   ├── ingress.yaml
│   └── secret.yaml
├── scripts/                   # Scripts de automatización y herramientas de testing
│   ├── generate_openapi.py
│   ├── run_tests.ps1
│   └── run_tests.sh
├── src/                       # Código principal de la aplicación
│   ├── __init__.py
│   ├── main.py                # Inicialización de la aplicación FastAPI y registro del outbox worker
│   ├── api/                   # Capa de controladores y rutas HTTP
│   │   ├── __init__.py
│   │   ├── health.py          # Endpoint para monitoreo de salud
│   │   ├── controllers/
│   │   │   └── __init__.py
│   │   └── routers/
│   │       ├── __init__.py
│   │       └── payment_router.py
│   ├── core/                  # Configuraciones básicas, base de datos, logs y telemetría
│   │   ├── __init__.py
│   │   ├── config.py          # Configuración de variables de entorno (incluye knobs de outbox)
│   │   ├── database.py        # Configuración de SQLAlchemy y sesiones de DB
│   │   ├── dependencies.py    # Inyección de dependencias para FastAPI
│   │   ├── logging.py         # Configuración centralizada de logs
│   │   ├── observability.py   # Configuración de métricas y monitoreo
│   │   ├── telemetry.py       # Inicialización de OpenTelemetry
│   │   └── utils.py           # Funciones de utilidad general
│   ├── exceptions/            # Manejadores de excepciones y errores custom
│   │   └── __init__.py
│   ├── models/                # Modelos de base de datos (SQLAlchemy)
│   │   ├── __init__.py
│   │   ├── outbox_event.py    # Modelo para la tabla outbox_events
│   │   └── payment.py
│   ├── repositories/          # Patrón repositorio para acceso y queries a la DB
│   │   ├── __init__.py
│   │   ├── outbox_repository.py  # Consultas atómicas y reclamo de lotes de outbox
│   │   └── payment_repository.py
│   ├── schemas/               # Validaciones y esquemas de datos (Pydantic)
│   │   ├── __init__.py
│   │   └── payment.py
│   └── services/              # Lógica de negocio y clientes de integración (MP, Órdenes)
│       ├── __init__.py
│       ├── mercadopago_client.py  # Integración con la API de MercadoPago
│       ├── orders_client.py       # Cliente HTTP para notificar al MS de Órdenes
│       ├── outbox_worker.py       # Tarea en segundo plano y despachador inline de eventos
│       ├── payment_service.py     # Lógica core de orquestación de pagos
│       └── webhook_validator.py   # Validación de firmas del Webhook de MercadoPago
├── tests/                     # Suite de pruebas automatizadas (unitarias y de integración)
│   ├── __init__.py
│   ├── conftest.py            # Configuración de fixtures de pruebas y mocks de base de datos
│   ├── test_debug_webhook.py
│   ├── test_health.py
│   ├── test_mercadopago_client.py
│   ├── test_model_payment.py
│   ├── test_observability.py
│   ├── test_orders_client.py
│   ├── test_outbox.py         # Pruebas para validar el outbox transaccional y el worker
│   ├── test_payment_status.py
│   ├── test_preferences.py
│   ├── test_refund.py
│   ├── test_repository.py
│   └── test_webhook.py
├── .dockerignore
├── .env.example
├── .gitignore
├── Dockerfile                 # Dockerfile de producción para el microservicio
├── Dockerfile.test            # Dockerfile para la ejecución de la suite de pruebas
├── docker-compose.test.yml    # Composición Docker para levantar DB de testing y correr suite
├── openapi.json               # Especificación OpenAPI generada
├── pyproject.toml
└── requirements.txt           # Dependencias del proyecto
```

---

## 🛠️ Stack Tecnológico

- **Python 3.11+**
- **PostgreSQL 15+**
- `fastapi`, `uvicorn[standard]`
- `sqlalchemy`, `psycopg2-binary`
- `pydantic-settings`
- `httpx`

**Testing:**
- `pytest`, `pytest-asyncio`, `pytest-httpx`
- Los tests de integración levantan una base de datos real en Docker (`docker-compose.test.yml`) y mockean todas las peticiones salientes con `pytest-httpx`.

---

## 🔑 Variables de Entorno

El servicio requiere la configuración de las siguientes variables para inicializarse:

| Variable | Descripción |
|----------|-------------|
| `APP_NAME` | Nombre de la app (Ej: "Payments Microservice"). |
| `DATABASE_URL` | DSN de PostgreSQL (Ej: `postgresql://user:pass@db:5432/payments`). |
| `MP_ACCESS_TOKEN` | Token de Producción/Test provisto por MercadoPago. |
| `MP_WEBHOOK_SECRET` | Secreto asignado por MercadoPago para validación HMAC-SHA256 del webhook. |
| `ORDERS_SERVICE_URL` | URL base del MS de Órdenes interno (Ej: `http://orders-service:8000`). |

*(Importante: Estas variables se inyectan en K8s vía Kubernetes Secrets).*

---

## 📡 Endpoints Principales

- **`POST /payments/preferences`**: Solicita la generación de una URL de Checkout (Idempotente).
- **`GET /payments/{order_id}/status`**: Endpoint de polling para consultar el estado interno (PENDING, APPROVED, REJECTED).
- **`POST /payments/mp-webhook`**: Endpoint público sin autenticación, asegurado mediante firmas HMAC, que recibe las notificaciones asíncronas de MercadoPago.
