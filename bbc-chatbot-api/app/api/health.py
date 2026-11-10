"""Health.

GET /health          public, for the Railway healthcheck and uptime pings:
                     `{"status": "ok"}` and nothing else.
GET /health/detail   the counters (and, with ?full=true, the live DB checks).
                     CRON_SECRET bearer, or a session of owner/admin/dev.
GET /health/ip-echo  what client_ip() sees for this request — the production
                     IP test and the hourly self-test. CRON_SECRET only.
"""

import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from config.settings import settings
from app.security.client_ip import CLIENT_IP_HEALTH, client_ip
from app.db.supabase import check_connection, supervisor_columns_status
from app.services.scheduler import get_scheduler_health

logger = logging.getLogger(__name__)

router = APIRouter()

_OPS_ROLES = frozenset({"owner", "admin", "dev"})


def _is_cron_bearer(request: Request) -> bool:
    secret = (settings.cron_secret or "").strip()
    if not secret:
        return False
    auth = request.headers.get("Authorization", "")
    return hmac.compare_digest(auth.encode("utf-8"), f"Bearer {secret}".encode("utf-8"))


def require_cron(request: Request) -> None:
    if not _is_cron_bearer(request):
        raise HTTPException(status_code=401, detail="Authentication required")


def require_ops(request: Request) -> None:
    """CRON_SECRET, or a logged-in owner/admin/dev."""
    if _is_cron_bearer(request):
        return
    from app.security.auth import get_current_user

    user = get_current_user(request)  # 401 on anything that is not a session
    if user.get("role") not in _OPS_ROLES:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.get("/health")
async def health() -> dict:
    """Liveness only. Everything internal moved to /health/detail."""
    return {"status": "ok"}


@router.get("/health/ip-echo", dependencies=[Depends(require_cron)])
async def ip_echo(request: Request) -> dict:
    from app.security.client_ip import source_ip

    # source_ip: the configured header alone, which the self-test judges;
    # client_ip may be the spoofable fallback and would hide the problem.
    return {"client_ip": client_ip(request), "source_ip": source_ip(request)}


@router.get("/health/detail", dependencies=[Depends(require_ops)])
async def health_detail(full: bool = False) -> dict:
    """Counters by default (no DB calls, safe to poll); live checks with ?full=true."""
    from app.flags import FLAGS_HEALTH

    scheduler = get_scheduler_health()
    # Always surface: a sticky False here freezes activity clocks and poisons
    # derived tags (false no_engagement). Ops must see it without log diving.
    supervisor_columns = supervisor_columns_status()
    # Money-path counter: lead writes must never fail silently.
    from app.services.lead_service import LEAD_WRITE_HEALTH
    # Provider fallback counter: primary-model failures must be visible.
    from app.ai.claude import AI_FALLBACK_HEALTH
    # Missed handoffs: every count is a client who asked for a human.
    from app.services.handoff import HANDOFF_HEALTH
    # Generation cascade deaths: each count is a client who got a template
    # because every model failed.
    from app.pipeline.generator import GENERATION_HEALTH
    # CRM push truth: ok = proven (2xx+id); failed/refused = flag stayed false.
    from app.services.crm import CRM_PUSH_HEALTH
    # Executor saturation: the thing nothing measured while we blamed the network
    # for two weeks. peak_in_flight near `workers` is the cliff, not a symptom.
    from app.db.supabase import db_health_snapshot, DASHBOARD_CACHE_HEALTH, _DASH_RAW
    from app.api.agent import DEADLINE_HEALTH, heartbeat_health_snapshot
    # Live SSE subscribers: the panel and the widget share a conversation's
    # fan-out now, so a count that never rises means the panel is still polling.
    from app.realtime.manager import manager as _sse
    # Presence gate: aggregate only — never who was blocked, or when.
    from app.api.integration import PRESENCE_GATE_HEALTH
    # Claim races: exactly one wins; the losers are counted, not punished.
    from app.api.conversations import CLAIM_HEALTH
    # The claim gate's write failed or came back empty, and the owner read
    # said whose the chat is: won_after_check = a Take that used to read as
    # lost ("… took it a moment before you") although the agent had it.
    from app.db.supabase import CLAIM_GATE_HEALTH
    # CRM sign-ins: one per CRM page (the dock reopens the panel) — counted,
    # not logged; refusals by reason.
    from app.api.auth_routes import SSO_HEALTH
    # What the log said today: warnings and errors by kind, held back or not.
    from app.log_setup import log_health_snapshot
    # Blind take: checks that failed closed (an agent got a 503, not their
    # chat) and streams stopped because their chat started waiting.
    from app.api.conversations import BLIND_HEALTH
    # Sticky's only defence is the deadline — these two say if it is holding.
    from app.services.routing import STICKY_HEALTH
    # Clients who asked for a person: queued, or NOT in the line (write_failed,
    # put_back_failed; last_failed_code 22001/23514 = migration 052 missing).
    from app.services.routing import QUEUE_HEALTH
    # Turns we refused to speak: every count is a moment the bot was about to
    # answer a question nobody asked.
    from app.pipeline.orchestrator import PIPELINE_HEALTH, BG_HEALTH
    from app.api.cron import _DB_ALERT
    # The owner's Dashboard: one paged read a minute, shared; failures 502.
    from app.api.dashboard import OWNER_DASHBOARD_HEALTH
    # The Leads page's shared read: how often it reads, and how many rows.
    from app.db.supabase import LEADS_UNIVERSE_HEALTH
    # To read (Conversations): one shared read a minute; failures 502.
    from app.api.conversations import TO_READ_HEALTH
    # Team · Now: one shared read every 10 s; failures 502.
    from app.api.teams import TEAM_NOW_HEALTH
    # Tasks: promises caught, call-backs, moves on Not ready, and failures
    # (schema_missing until migration 051 runs).
    from app.db.supabase import TASKS_HEALTH
    # KB search by meaning: failed = turns answered from keyword search
    # instead; last_error = what Qdrant said.
    from app.db.qdrant import QDRANT_HEALTH
    # Online minutes: ticks skipped because the day could not be read or saved.
    from app.services.presence import PRESENCE_HEALTH
    # The abandoned sweep's scan: one read a tick (embedded), or the old
    # read-per-conversation (message_reads) when that one is refused.
    from app.db.supabase import ABANDONED_SCAN_HEALTH
    # Sign-in: the users table could not be read (answered "try again", not
    # "no such account").
    from app.db.supabase import USER_LOOKUP_HEALTH

    from app.ai.ledger import LEDGER_HEALTH
    from app.ai.output_guard import OUTPUT_GUARD_HEALTH
    from app.security.decoys import DECOY_HEALTH
    from app.security.gates import GATE_HEALTH
    from app.pipeline.slots import SLOTS_HEALTH
    from app.security.ban_guard import FAIL2BAN_HEALTH
    from app.security.visitor_token import VISITOR_TOKEN_HEALTH
    from app.security.turnstile import TURNSTILE_HEALTH
    from app.security.form_trap import FORM_TRAP_HEALTH
    from app.security.mfa import MFA_HEALTH
    from app.services.retention import RETENTION_HEALTH
    from app.security.behavior import BEHAVIOR_HEALTH
    from app.learning.outcomes import OUTCOMES_HEALTH
    from app.security.token_version import TOKEN_VERSION_HEALTH

    if not full:
        return {
            "status": "ok",
            "flags": dict(FLAGS_HEALTH),
            "client_ip": dict(CLIENT_IP_HEALTH),
            "cost": dict(LEDGER_HEALTH),
            "decoys": dict(DECOY_HEALTH),
            "role_gates": dict(GATE_HEALTH),
            "output_guard": dict(OUTPUT_GUARD_HEALTH),
            "slots": dict(SLOTS_HEALTH),
            "fail2ban": dict(FAIL2BAN_HEALTH),
            "visitor_token": dict(VISITOR_TOKEN_HEALTH),
            "turnstile": dict(TURNSTILE_HEALTH),
            "form_trap": dict(FORM_TRAP_HEALTH),
            "mfa": dict(MFA_HEALTH),
            "retention": dict(RETENTION_HEALTH),
            "behavior": dict(BEHAVIOR_HEALTH),
            "learning_outcomes": dict(OUTCOMES_HEALTH),
            "token_version": dict(TOKEN_VERSION_HEALTH),
            "scheduler": scheduler,
            "supervisor_columns": supervisor_columns,
            "lead_writes": dict(LEAD_WRITE_HEALTH),
            "ai_fallback": dict(AI_FALLBACK_HEALTH),
            "handoffs_expired": dict(HANDOFF_HEALTH),
            "claims": dict(CLAIM_HEALTH),
            "claim_gate": dict(CLAIM_GATE_HEALTH),
            "sso": dict(SSO_HEALTH),
            "logs": log_health_snapshot(),
            "blind_take": dict(BLIND_HEALTH),
            "sticky": dict(STICKY_HEALTH),
            "needs_agent": dict(QUEUE_HEALTH),
            "generation_fallbacks": dict(GENERATION_HEALTH),
            "crm_pushes": dict(CRM_PUSH_HEALTH),
            "db": db_health_snapshot(),
            "dashboard_cache": {
                **DASHBOARD_CACHE_HEALTH,
                # When any of these reaches 10,000 we are truncating (oldest first).
                "rows": {k: len(_DASH_RAW[k]) for k in ("convos", "leads", "runs")},
            },
            "background": dict(BG_HEALTH),
            "db_alert": {"over_streak": _DB_ALERT["over_streak"], "episode_open": _DB_ALERT["episode_open"]},
            "heartbeat": heartbeat_health_snapshot(),
            "deadline_skips": dict(DEADLINE_HEALTH),
            "sse": {"subscribers": _sse.subscriber_count()},
            "presence_gate": dict(PRESENCE_GATE_HEALTH),
            "phantom_turns": dict(PIPELINE_HEALTH),
            "owner_dashboard": dict(OWNER_DASHBOARD_HEALTH),
            "leads_universe": dict(LEADS_UNIVERSE_HEALTH),
            "to_read": dict(TO_READ_HEALTH),
            "team_now": dict(TEAM_NOW_HEALTH),
            "tasks": dict(TASKS_HEALTH),
            "kb_search": dict(QDRANT_HEALTH),
            "presence_ticks": dict(PRESENCE_HEALTH),
            "abandoned_scan": dict(ABANDONED_SCAN_HEALTH),
            "user_lookup": dict(USER_LOOKUP_HEALTH),
        }

    # Check Supabase connectivity
    db_ok = await check_connection()

    # Queue visibility — DB-backed, so only in the full (debug) payload; the
    # lightweight payload keeps to in-memory counters (UptimeRobot pings it).
    from app.db import supabase as _db
    _queue_stats = await _db.get_queue_stats()
    _load = await _db.get_operator_load(settings.queue_presence_window_seconds)
    _thr = settings.operator_load_threshold
    _load["over_threshold"] = sum(
        1 for v in _load["per_agent"].values() if v["active"] > _thr
    )

    # Check service configuration (don't make live calls)
    claude_ok = bool(settings.anthropic_api_key)
    qdrant_configured = bool(settings.qdrant_url)
    redis_configured = bool(settings.redis_url)

    services = {
        "supabase": "connected" if db_ok else "disconnected",
        "claude": "configured" if claude_ok else "missing_key",
        "qdrant": "configured" if qdrant_configured else "disabled",
        "redis": "configured" if redis_configured else "disabled",
    }

    # Determine overall status
    if not db_ok:
        status = "critical"
    elif not claude_ok:
        status = "degraded"
    else:
        status = "healthy"

    return {
        "status": status,
        "version": "1.0.0",
        "services": services,
        "flags": dict(FLAGS_HEALTH),
        "client_ip": dict(CLIENT_IP_HEALTH),
        "cost": dict(LEDGER_HEALTH),
        "decoys": dict(DECOY_HEALTH),
        "role_gates": dict(GATE_HEALTH),
        "output_guard": dict(OUTPUT_GUARD_HEALTH),
        "slots": dict(SLOTS_HEALTH),
        "fail2ban": dict(FAIL2BAN_HEALTH),
        "visitor_token": dict(VISITOR_TOKEN_HEALTH),
        "turnstile": dict(TURNSTILE_HEALTH),
        "form_trap": dict(FORM_TRAP_HEALTH),
        "mfa": dict(MFA_HEALTH),
        "retention": dict(RETENTION_HEALTH),
        "behavior": dict(BEHAVIOR_HEALTH),
        "learning_outcomes": dict(OUTCOMES_HEALTH),
        "token_version": dict(TOKEN_VERSION_HEALTH),
        "scheduler": scheduler,
        "supervisor_columns": supervisor_columns,
        "lead_writes": dict(LEAD_WRITE_HEALTH),
        "ai_fallback": dict(AI_FALLBACK_HEALTH),
        "handoffs_expired": dict(HANDOFF_HEALTH),
        "claims": dict(CLAIM_HEALTH),
        "claim_gate": dict(CLAIM_GATE_HEALTH),
        "sso": dict(SSO_HEALTH),
        "logs": log_health_snapshot(),
        "blind_take": dict(BLIND_HEALTH),
        "sticky": dict(STICKY_HEALTH),
        "needs_agent": dict(QUEUE_HEALTH),
        "queue": _queue_stats,
        "operator_load": _load,
        "generation_fallbacks": dict(GENERATION_HEALTH),
        "crm_pushes": dict(CRM_PUSH_HEALTH),
        "db": db_health_snapshot(),
        "dashboard_cache": {
            **DASHBOARD_CACHE_HEALTH,
            # When any of these reaches 10,000 we are truncating (oldest first).
            "rows": {k: len(_DASH_RAW[k]) for k in ("convos", "leads", "runs")},
        },
        "background": dict(BG_HEALTH),
        "db_alert": {"over_streak": _DB_ALERT["over_streak"], "episode_open": _DB_ALERT["episode_open"]},
        "heartbeat": heartbeat_health_snapshot(),
        "deadline_skips": dict(DEADLINE_HEALTH),
        "sse": {"subscribers": _sse.subscriber_count()},
        "presence_gate": dict(PRESENCE_GATE_HEALTH),
        "phantom_turns": dict(PIPELINE_HEALTH),
        "owner_dashboard": dict(OWNER_DASHBOARD_HEALTH),
        "leads_universe": dict(LEADS_UNIVERSE_HEALTH),
        "to_read": dict(TO_READ_HEALTH),
        "team_now": dict(TEAM_NOW_HEALTH),
        "tasks": dict(TASKS_HEALTH),
        "kb_search": dict(QDRANT_HEALTH),
        "presence_ticks": dict(PRESENCE_HEALTH),
        "abandoned_scan": dict(ABANDONED_SCAN_HEALTH),
        "user_lookup": dict(USER_LOOKUP_HEALTH),
    }
