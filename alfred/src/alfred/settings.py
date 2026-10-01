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
    # Price estimate for the cost metric only (USD per million tokens, Haiku 4.5 list price);
    # override in the environment if the model or the price changes.
    llm_input_usd_per_mtok: float = 1.0
    llm_output_usd_per_mtok: float = 5.0
    usd_to_eur: float = 0.92
    aws_region: str = "eu-central-1"  # Bedrock only — Frankfurt, data residency

    # Time — every "today"/"this week" and reminder time is in this zone
    timezone: str = "Europe/Amsterdam"

    # Dashboard / public pages
    privacy_contact_email: str = ""  # shown on /privacy
    base_url: str = ""  # e.g. https://alfred.up.railway.app
    dashboard_token_ttl_days: int = 90  # older links stop working; "meu dashboard" issues a new one
    dashboard_rate_limit_per_minute: int = 60  # per client IP, token routes only

    # Observability / ops
    sentry_dsn: str = ""  # empty → error tracking off
    internal_metrics_token: SecretStr = SecretStr("")  # empty → /internal/metrics is a 404

    # Expenses at or above this amount get an explicit "is this right?" hint (undo via
    # "apaga" / "errei foram X"). Nothing is blocked: a hint costs no state and no round trip.
    high_value_threshold: float = 1000.0

    # V2-02: bill reminders outside the 24 h window need the approved template
    # ``alfred_payment_reminder``. Off until Meta approves it (the code is ready and tested).
    payment_reminder_template_enabled: bool = False


settings = Settings()
