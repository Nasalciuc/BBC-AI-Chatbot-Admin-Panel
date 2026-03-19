"""Centralized configuration — all settings loaded from environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # API
    app_name: str = "BBC Chatbot API"
    debug: bool = False
    cors_origins: list[str] = [
        "https://buybusinessclass.com",
        "https://www.buybusinessclass.com",
        "http://localhost:5173",
        "http://localhost:5174",
        "https://bbc-admin.vercel.app",
        "https://admin-panel-error.vercel.app",
    ]

    # Claude
    anthropic_api_key: str = ""  # Optional for dev — required only for AI pipeline
    claude_haiku_model: str = "claude-haiku-4-5-20251001"
    claude_sonnet_model: str = "claude-sonnet-4-20250514"
    claude_timeout: int = 8

    # Supabase
    supabase_url: str  # REQUIRED
    supabase_key: str  # REQUIRED

    # Qdrant (optional — empty = skip vector search)
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection: str = "bbc_kb"
    qdrant_enabled: bool = False

    # Redis (optional — empty = skip rate limiting)
    redis_url: str = ""

    # Auth (required in production — empty = no auth in dev)
    api_user: str = ""
    api_pass: str = ""

    # JWT
    jwt_secret: str = ""  # REQUIRED in production for auth
    jwt_expiry_hours: int = 24

    # Budget
    daily_budget: float = 50.0
    per_conversation_budget: float = 0.50
    budget_alert_threshold: float = 0.70

    # Rate Limiting
    rate_burst: int = 15
    rate_sustained_seconds: int = 3
    rate_hourly_max: int = 100
    rate_daily_max: int = 300

    # Pipeline
    pipeline_timeout: int = 10
    max_message_length: int = 2000
    max_messages_per_conversation: int = 50

    # CRM
    crm_api_url: str = "https://crm.buybusinessclass.com/ai"
    crm_api_token: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
