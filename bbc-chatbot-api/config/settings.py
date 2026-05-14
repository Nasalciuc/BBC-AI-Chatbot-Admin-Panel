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
    claude_timeout: int = 5

    # Supabase
    supabase_url: str  # REQUIRED
    supabase_key: str  # REQUIRED

    # Qdrant (optional — empty = skip vector search)
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection: str = "kb_entries"
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
    pipeline_timeout: int = 20
    max_message_length: int = 2000
    max_messages_per_conversation: int = 50

    # Agent Presence & Routing
    max_concurrent_chats: int = 1
    agent_timeout_seconds: int = 600
    agent_silent_timeout_seconds: int = 300

    # System messages shown during routing flow
    connecting_message: str = "Connecting you with a specialist now\u2026"
    joined_message_template: str = "You\u2019re now being assisted by {agent_name}."
    affinity_welcome_back_template: str = (
        "Welcome back! {agent_name} is joining you shortly."
    )

    # Welcome messages per tunnel
    welcome_message_sales: str = "Where would you like to fly? I\u2019ll find you the best business class options."
    welcome_message_support: str = "What can I help you with today?"

    # Quick reply suggestions per tunnel
    quick_replies_sales: list = ["Round-trip to Europe", "One-way flight", "Specific route quote", "Last-minute deal"]
    quick_replies_support: list = ["Change my booking", "Cancel or refund", "Flight status", "Other question"]

    # Messages shown to widget when heartbeat assigns an AI conv to a newly available agent
    heartbeat_joined_template: str = "{agent_name} has joined — they’ll take it from here."
    heartbeat_welcome_sales: str = "Let’s continue — where would you like to fly?"
    heartbeat_welcome_support: str = "Let’s continue — what can I help you with?"

    # CRM
    crm_api_url: str = "https://webapi.buybusinessclass.com"
    crm_api_token: str = ""

    # Postmark email
    postmark_token: str = ""
    email_from: str = "noreply@buybusinessclass.com"
    admin_panel_url: str = "https://admin-panel-error.vercel.app"
    invite_link_expiry_minutes: int = 30
    invite_link_path: str = "/set-password"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
