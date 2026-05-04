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
    APP_NAME: str = "template-service"
    LOG_LEVEL: str = "info"

    # ── Database ─────────────────────────────────────
    DB_HOST: str
    DB_PORT: int = 5432
    DB_USER: str
    DB_PASSWORD: str
    DB_NAME: str

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
            f"postgresql://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )


@lru_cache
def get_settings() -> Settings:
    """Return a cached singleton of the application settings."""
    return Settings()  # type: ignore[call-arg]
