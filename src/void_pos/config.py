from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VOIDPOS_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://localhost/void_pos"

    # Kitchen printing (optional; unset = orders project but nothing prints).
    kitchen_printer_host: str | None = None
    kitchen_printer_port: int = 9100
    kitchen_font_path: str | None = None  # a font with the glyphs for non-Latin tickets


settings = Settings()
