from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VOIDPOS_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://localhost/void_pos"


settings = Settings()
