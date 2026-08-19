"""Centralized configuration — all settings loaded from environment variables."""

import json

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings

# Always allowed — merged into cors_origins even when CORS_ORIGINS env omits BCT
REQUIRED_CORS_ORIGINS: tuple[str, ...] = (
    "https://businessclass-tickets.com",
    "https://www.businessclass-tickets.com",
    "https://buybusinessclass.com",
    "https://www.buybusinessclass.com",
    "https://crm.buybusinessclass.com",
)

_DEFAULT_CORS_ORIGINS: tuple[str, ...] = (
    *REQUIRED_CORS_ORIGINS,
    "http://localhost:5173",
    "http://localhost:5174",
    "https://bbc-admin.vercel.app",
    "https://admin-panel-error.vercel.app",
    "https://bbc-admin-panel-eight.vercel.app",
    "https://chat.buybusinessclass.com",
    "https://bbc-widget.vercel.app",
)


def _parse_cors_origins_env(raw: str) -> list[str]:
    """Parse CORS_ORIGINS from JSON array or comma-separated URLs."""
    s = (raw or "").strip()
    if not s:
        return list(_DEFAULT_CORS_ORIGINS)
    if s.startswith("["):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except json.JSONDecodeError:
            pass
    return [part.strip() for part in s.split(",") if part.strip()]


def _merge_required_cors_origins(origins: list[str]) -> list[str]:
    merged = list(REQUIRED_CORS_ORIGINS)
    for origin in origins:
        normalized = origin.strip().rstrip("/")
        if normalized and normalized not in merged:
            merged.append(normalized)
    return merged


class Settings(BaseSettings):
    # API
    app_name: str = "BBC Chatbot API"
    debug: bool = False
    # String env avoids pydantic-settings JSON-decoding list fields before validators run
    cors_origins_env: str = Field(default="", validation_alias="CORS_ORIGINS")

    # Claude
    anthropic_api_key: str = ""  # Optional for dev — required only for AI pipeline
    # Model tiers: haiku = utility (classification, extraction, support),
    # sonnet = sales default, opus = premium turns. Env-overridable so a tier
    # can be rolled back to an older model without a deploy.
    claude_haiku_model: str = "claude-haiku-4-5-20251001"
    claude_sonnet_model: str = "claude-sonnet-4-6"
    claude_opus_model: str = "claude-opus-4-8"
    claude_timeout: int = 15
    # Emergency fallback: on a non-timeout API error (overloaded/rate-limit)
    # the call retries ONCE on this cheaper model. The collection never dies
    # with the primary; /health.ai_fallback counts activations.
    fallback_model: str = "claude-haiku-4-5-20251001"

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
    # REQUIRED in production; the Bearer path in auth.py fails closed (500) if
    # empty and debug=False. Do NOT hard-crash on import — Railway needs /health.
    jwt_secret: str = ""
    jwt_expiry_hours: int = 24

    # Profile avatar uploads (Supabase Storage bucket — see AVATAR_STORAGE_SETUP.md)
    avatar_bucket: str = "avatars"
    avatar_max_bytes: int = 2_000_000  # 2MB

    # Budget
    daily_budget: float = 50.0
    per_conversation_budget: float = 0.50
    budget_alert_threshold: float = 0.70

    # Daily learning loop
    learning_run_budget: float = 5.0        # per daily run
    learning_bootstrap_budget: float = 15.0  # the one-off historical run
    learning_regression_alert_pct: float = 30.0  # abandoned share 7d vs prior 7d
    lesson_staleness_days: int = 30         # approved but unreinforced → out of the prompt
    lesson_injection_limit: int = 8
    learning_runs_retention_days: int = 90

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
    # Two DIFFERENT clocks — do not conflate them:
    #   agent_timeout_seconds        → presence: last_seen_at (heartbeat, or an
    #                                  agent message, which also refreshes it).
    #   agent_silent_timeout_seconds → engagement: assigned but hasn't spoken,
    #                                  and how fresh an agent message must be to
    #                                  count as presence on its own.
    agent_timeout_seconds: int = 600
    # Spec v2.4 §2.1 — THREE presence windows, three jobs, three names:
    #   agent_timeout_seconds (600s)        routing + sticky. Raised
    #     deliberately: a returning client must not lose their operator over a
    #     three-minute phone call (D2).
    #   crm_presence_window_seconds (90s)   the CRM gate. Unchanged.
    #   queue_presence_window_seconds (90s) THIS one — the shared queue.
    #     "Leave the CRM and you are out of the line in ninety seconds, without
    #     pressing anything." A ten-minute window would keep ghosts visible in
    #     the line for ten minutes, which is the disease we spent June curing.
    queue_presence_window_seconds: int = 90
    # Spec v2.4 §2ter/D4 — the three automatic drains are OFF once the shared
    # queue opens: close auto-assign, heartbeat assign, and dispatch_needs_agent.
    # Only one mechanism may distribute ownerless conversations, and it is the
    # queue: someone receiving work without pressing anything, while the others
    # watch a row vanish with no explanation, is the thing we removed.
    #
    # This flag is the rollback net for a single-PR deploy: flipping it to True
    # restores yesterday's distribution WITHOUT reverting the queue. The dead
    # code is deleted in ticket QUEUE-CLEANUP (one iteration) — dead code with a
    # deadline, not dead code that rots.
    auto_dispatch_enabled: bool = False
    # Load signal, never a barrier (spec v2.4 §6.9): above this many active
    # conversations an operator's row is flagged for the supervisor. Nothing is
    # blocked — the owners asked for competition, and a hidden cap would be
    # balancing by stealth.
    operator_load_threshold: int = 5
    agent_silent_timeout_seconds: int = 900  # was 480; an operator READING a long thread before replying must not be reverted
    # First-response timeout: operator must send first message after assignment.
    # If silent → AI takes over instantly. The 480s silent timeout above is for
    # operators who HAVE responded but then went silent mid-conversation.
    agent_first_response_timeout_seconds: int = 90  # was 30 (impossible for humans; p75≈480s per 30d audit). With FIX-A, AI serves during this window anyway.

    # System messages shown during routing flow
    connecting_message: str = "Connecting you with a specialist now\u2026"
    joined_message_template: str = "You\u2019re now being assisted by {agent_name}."
    affinity_welcome_back_template: str = (
        "Welcome back! {agent_name} is joining you shortly."
    )

    # Welcome messages per tunnel
    welcome_message_sales: str = "Where would you like to fly? I\u2019ll find you the best business class options."
    welcome_message_support: str = "What can I help you with today?"
    # Template for lead confirmation summary. Populated from lead dict,
    # shown to client BEFORE CRM submit. Client must reply "yes" to confirm.
    summary_template: str = (
        "Let me confirm your request:\n\n"
        "✈ {origin_code} → {destination_code}\n"
        "📅 {departure}{return_clause}\n"
        "👥 {passengers} passenger(s)\n"
        "💺 {cabin_class} class\n\n"
        "Is everything correct? Reply YES to confirm."
    )
    post_crm_closing_message: str = (
        "Your flight request is confirmed! A travel consultant "
        "will contact you shortly. For immediate help, "
        "call +1 (888) 322-7999."
    )

    # Quick reply suggestions per tunnel
    quick_replies_sales: list = ["Round-trip to Europe", "One-way flight", "Specific route quote", "Last-minute deal"]
    quick_replies_support: list = ["Change my booking", "Cancel or refund", "Flight status", "Other question"]

    # Message shown to widget when heartbeat assigns an AI conv to a newly available agent
    heartbeat_joined_template: str = "{agent_name} has joined — they’ll take it from here."

    # CRM
    crm_api_url: str = "https://webapi.buybusinessclass.com"
    crm_api_token: str = ""

    # Abuse blocklist — IP entries expire (shared IPs go stale); phone/email are
    # permanent by default. IP never blocks on its own (see services/blocklist.py).
    blocklist_ip_ttl_days: int = 30

    # Cron — abandoned conversations
    cron_secret: str = ""

    # CHAT_SSO_SECRET: shared with the CRM team, used ONLY to verify CRM-signed
    # login JWTs (docs/chatbot-sso.md). Distinct from JWT_SECRET (our own
    # session-signing secret). CRM signs, we verify → both sides use the SAME value.
    # Empty = CRM SSO disabled (503).
    chat_sso_secret: str = ""
    # CRM_PRESENCE_WINDOW_SECONDS: how fresh the agent's heartbeat must be for
    # the CRM presence gate to consider them online. The panel pings every 5s,
    # so 90s tolerates 18 missed beats — a network blink never blocks anyone.
    # Distinct from agent_timeout_seconds, which governs conversations falling
    # back to AI.
    crm_presence_window_seconds: int = 90
    abandoned_timeout_minutes: int = 30
    internal_scheduler_enabled: bool = True  # G3 kill-switch; see ADR-10
    scheduler_interval_seconds: int = 300  # real 5-min cadence (GitHub Actions
    # '*/5' measured at 1-2h due to schedule throttling on low-activity repos)

    # Postmark email
    postmark_token: str = ""
    email_from: str = "noreply@buybusinessclass.com"
    admin_panel_url: str = "https://chat.buybusinessclass.com"
    super_alert_email: str = "super@buybusinessclass.com"
    super_alert_cooldown_minutes: int = 1440  # 1 email/conv/24h (Postmark limit)
    # 48h: a 24h link that lands in a Friday inbox is dead by Monday
    # (Mitch never used his). Long enough to survive a weekend.
    invite_link_expiry_minutes: int = 2880
    invite_link_path: str = "/set-password"

    # Supervisor visibility suite — customer silence (Inactive tag) and
    # attention emails. These are about the CUSTOMER going quiet / a chat
    # needing a human, NOT the rejected 5-minute Fresh / 15-minute pool rules.
    inactive_quiet_minutes: int = 30
    attention_email_enabled: bool = True
    attention_email_to: str = ""  # empty → falls back to super_alert_email

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origins(self) -> list[str]:
        """Resolved CORS allowlist: env + required BCT/BBC domains."""
        return _merge_required_cors_origins(_parse_cors_origins_env(self.cors_origins_env))

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
