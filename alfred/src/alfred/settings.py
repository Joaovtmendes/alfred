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

    # LLM
    llm_provider: str = "bedrock"          # bedrock | azure_openai
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    aws_region: str = "eu-central-1"        # Frankfurt — data residency

    # Security
    secret_key: SecretStr = SecretStr("change-me-in-production")


settings = Settings()
