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
    # Delivery-aggregator webhook: a shared secret the incoming request carries in the
    # X-Void-Aggregator-Secret header. Unset = the webhook is off (503). The real partner
    # adapters verify each platform's signed webhooks instead; this is the open-core seam.
    aggregator_webhook_secret: SecretStr | None = None

    # --- online ordering, CRM, loyalty, marketing (open-core activation) -------------
    # The customer-facing storefront (the public /online/* API). Off = those routes 404.
    online_ordering_enabled: bool = True

    # Loyalty earn/redeem on the existing ledger. Off until enabled (points neither earned
    # nor redeemable); the storefront hides points entirely when off.
    loyalty_enabled: bool = False
    loyalty_earn_points_per_dollar: int = Field(default=1, ge=0, le=1000)  # points per whole AUD
    loyalty_redeem_cents_per_point: int = Field(default=1, ge=1, le=100)  # a point's value in cents

    # Campaign senders: "log" (no-op, the open-core default), "twilio" (SMS) or
    # "sendgrid" (email). The real adapters are config-driven and use the venue's account.
    marketing_sms_sender: str = "log"
    marketing_email_sender: str = "log"
    twilio_account_sid: str | None = None
    twilio_auth_token: SecretStr | None = None
    twilio_from_number: str | None = None
    sendgrid_api_key: SecretStr | None = None
    sendgrid_from_email: str | None = None

    @field_validator(
        "admin_token",
        "kitchen_printer_host",
        "kitchen_font_path",
        "receipt_printer_host",
        "stripe_secret_key",
        "stripe_publishable_key",
        "square_access_token",
        "twilio_account_sid",
        "twilio_auth_token",
        "twilio_from_number",
        "sendgrid_api_key",
        "sendgrid_from_email",
        "aggregator_webhook_secret",
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
    def aggregator_enabled(self) -> bool:
        return self.aggregator_webhook_secret is not None

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()


def get_settings() -> Settings:
    """FastAPI dependency, so tests can swap the settings for one app instance."""
    return settings
