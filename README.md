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
   - *Nota de diseño:* Para sistemas de producción, este fallo permanente debería emitir una métrica/alerta y registrarse en una "Dead Letter Queue" para que un cronjob posterior concilie las órdenes que están pagadas pero que el MS de órdenes aún considera pendientes.

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
