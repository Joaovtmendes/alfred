"""Application settings — all config from environment variables via pydantic-settings."""

from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    environment: str = "development"
    log_level: str = "INFO"

    # Database
    database_url: str = "postgresql+asyncpg://alfred:alfred_dev@localhost:5432/alfred"

    # WhatsApp / Meta
    whatsapp_token: SecretStr = SecretStr("")
    whatsapp_verify_token: str = "dev_verify_token"
    whatsapp_app_secret: SecretStr = SecretStr("")
    whatsapp_phone_number_id: str = ""
    whatsapp_waba_id: str = ""  # only for scripts (template submission)
    graph_api_version: str = "v25.0"  # supported until 2028-07-29

    # LLM
    llm_provider: str = "anthropic"  # anthropic | bedrock
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = "claude-haiku-4-5-20251001"
    llm_timeout_seconds: float = 20.0  # per request; the SDK default is 600 s
    aws_region: str = "eu-central-1"  # Bedrock only — Frankfurt, data residency

    # Time — every "today"/"this week" and reminder time is in this zone
    timezone: str = "Europe/Amsterdam"

    # Dashboard
    base_url: str = ""  # e.g. https://alfred.up.railway.app

    # Security
    secret_key: SecretStr = SecretStr("change-me-in-production")


settings = Settings()
