"""
Application settings loaded from environment variables.

Uses pydantic-settings so every value comes from the environment (or a .env
file during local development).  **No credentials are ever hard-coded.**
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Centralised configuration for the microservice."""

    # ── Application ──────────────────────────────────
    APP_NAME: str = "payments-service"
    LOG_LEVEL: str = "info"
    WEBHOOK_REQUIRE_SIGNATURE: bool = False  # Set to True require valid signatures
    # ── Database ─────────────────────────────────────
    DB_HOST: str
    DB_PORT: int = 5432
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str

    # ── MercadoPago ──────────────────────────────────
    MP_ACCESS_TOKEN: str
    MP_WEBHOOK_SECRET: str
    WEBHOOK_BASE_URL: str = "https://payments.example.com"
    BAZAAR_BASE_URL: str = "bazaar://order-polling"

    # ── Internal Services ────────────────────────────
    ORDERS_SERVICE_URL: str

    # ── Outbox Worker ────────────────────────────────
    OUTBOX_WORKER_ENABLED: bool = True
    OUTBOX_WORKER_INTERVAL_SECONDS: int = 10
    OUTBOX_WORKER_BATCH_SIZE: int = 20
    OUTBOX_MAX_ATTEMPTS: int = 5

    # ── Pydantic-settings config ─────────────────────
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    @property
    def database_url(self) -> str:
        """Build the PostgreSQL connection DSN from individual fields."""
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton of the application settings."""
    return Settings()  # type: ignore[call-arg]
