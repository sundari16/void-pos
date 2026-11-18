from __future__ import annotations

from typing import Any

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_ADMIN_TOKEN_LENGTH = 24


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VOIDPOS_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://localhost/void_pos"

    # Venue admin bearer token: device registration, menu edits, export. Unset = admin off.
    admin_token: SecretStr | None = None
    # Browser origins allowed to call the API, comma-separated.
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    # How long a till pairing code stays redeemable.
    pairing_code_ttl_minutes: int = Field(default=15, ge=1, le=24 * 60)

    # Kitchen printing (optional; unset = orders project but nothing prints).
    kitchen_printer_host: str | None = None
    kitchen_printer_port: int = Field(default=9100, ge=1, le=65535)
    kitchen_font_path: str | None = None  # a font with the glyphs for non-Latin tickets

    @field_validator("admin_token", "kitchen_printer_host", "kitchen_font_path", mode="before")
    @classmethod
    def _blank_is_unset(cls, value: Any) -> Any:
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("admin_token")
    @classmethod
    def _admin_token_is_strong(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < MIN_ADMIN_TOKEN_LENGTH:
            raise ValueError(
                f"VOIDPOS_ADMIN_TOKEN must be at least {MIN_ADMIN_TOKEN_LENGTH} characters"
            )
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()


def get_settings() -> Settings:
    """FastAPI dependency, so tests can swap the settings for one app instance."""
    return settings
