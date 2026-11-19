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

    # Customer receipts (optional; unset = the till shows the receipt to print another way).
    receipt_printer_host: str | None = None
    receipt_printer_port: int = Field(default=9100, ge=1, le=65535)
    receipt_printer_columns: int = Field(default=42, ge=24, le=64)  # 42 for 80 mm, 32 for 58 mm

    # Stripe card payments on the till: SANDBOX keys only (sk_test_ / pk_test_). Unset = off.
    stripe_secret_key: SecretStr | None = None
    stripe_publishable_key: str | None = None
    # Square: an access token, only to read the item library for the menu import.
    square_access_token: SecretStr | None = None

    # Xero daily-takings sync (optional). The venue's own Xero app, OAuth2 auth-code flow:
    # the client id/secret of a Xero app the venue owns, and the redirect URI registered on
    # it. Unset = off. See docs/self-host.md "Accounting (Xero)".
    xero_client_id: SecretStr | None = None
    xero_client_secret: SecretStr | None = None
    xero_redirect_uri: str = "http://localhost:8000/xero/callback"
    xero_scopes: str = (
        "offline_access accounting.transactions accounting.contacts accounting.settings"
    )

    @field_validator(
        "admin_token",
        "kitchen_printer_host",
        "kitchen_font_path",
        "receipt_printer_host",
        "stripe_secret_key",
        "stripe_publishable_key",
        "square_access_token",
        "xero_client_id",
        "xero_client_secret",
        mode="before",
    )
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

    @field_validator("stripe_secret_key")
    @classmethod
    def _stripe_sandbox_only(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().startswith("sk_test_"):
            raise ValueError("VOIDPOS_STRIPE_SECRET_KEY must be a sandbox key (sk_test_...)")
        return value

    @field_validator("stripe_publishable_key")
    @classmethod
    def _stripe_publishable_sandbox_only(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith("pk_test_"):
            raise ValueError("VOIDPOS_STRIPE_PUBLISHABLE_KEY must be a sandbox key (pk_test_...)")
        return value

    @property
    def stripe_enabled(self) -> bool:
        return self.stripe_secret_key is not None and self.stripe_publishable_key is not None

    @property
    def xero_enabled(self) -> bool:
        return self.xero_client_id is not None and self.xero_client_secret is not None

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()


def get_settings() -> Settings:
    """FastAPI dependency, so tests can swap the settings for one app instance."""
    return settings
