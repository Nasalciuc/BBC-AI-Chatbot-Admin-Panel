"""Supabase DB client — all queries for BBC Chatbot.
Client: supabase-py (HTTP). NO asyncpg. NO SQLAlchemy.
"""
import asyncio
import logging
import statistics
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Optional, Any

from supabase import create_client, Client
from config.settings import settings

logger = logging.getLogger(__name__)

_client: Optional[Client] = None

# Dedicated thread pool for sync supabase-py calls (D-01)
_executor = ThreadPoolExecutor(max_workers=20)


async def _run_sync(fn):
    """Run a sync supabase-py call on thread pool to avoid blocking event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, fn)


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(settings.supabase_url, settings.supabase_key)
    return _client


# ════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ════════════════════════════════════════════════════════════════

async def check_connection() -> bool:
    """Check if Supabase is reachable."""
    try:
        db = get_client()
        await _run_sync(lambda: db.table("kb_categories").select("id").limit(1).execute())
        return True
    except Exception:
        return False


# ════════════════════════════════════════════════════════════════
# CONVERSATIONS — pipeline (existing + complete)
# ════════════════════════════════════════════════════════════════

async def get_or_create_conversation(
    conversation_id: Optional[str],
    tunnel: str,
    visitor: Any,
) -> Optional[dict]:
    """Return existing conversation or create a new one."""
    try:
        db = get_client()
        if conversation_id:
            res = await _run_sync(lambda: db.table("conversations").select("*").eq("id", conversation_id).single().execute())
            if res.data:
                return res.data
        payload: dict = {"tunnel": tunnel, "mode": "ai", "status": "active"}
        if visitor and visitor.name:  payload["visitor_name"]  = visitor.name
        if visitor and visitor.email: payload["visitor_email"] = visitor.email
        if visitor and visitor.phone: payload["visitor_phone"] = visitor.phone
        res = await _run_sync(lambda: db.table("conversations").insert(payload).execute())
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"get_or_create_conversation error: {e}")
        return None


async def add_message(
    conversation_id: str,
    role: str,
    content: str,
    model_used: Optional[str] = None,
    cost: float = 0.0,
) -> Optional[dict]:
    """Insert a message. Never loses a message."""
    try:
        db = get_client()
        payload: dict = {"conversation_id": conversation_id, "role": role, "content": content, "cost": cost}
        if model_used:
            payload["model_used"] = model_used
        res = await _run_sync(lambda: db.table("messages").insert(payload).execute())
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"add_message error: {e}")
        return None


async def count_messages(conversation_id: str) -> int:
    try:
        db = get_client()
        res = await _run_sync(lambda: db.table("messages").select("id", count="exact").eq("conversation_id", conversation_id).execute())  # type: ignore[arg-type]
        return res.count or 0
    except Exception:
        return 0


async def get_recent_messages(conversation_id: str, limit: int = 5) -> list:
    """Last N messages from a conversation (for AI context)."""
    try:
        db = get_client()
        def _query():
            return (
                db.table("messages")
                .select("role,content,model_used,created_at")
                .eq("conversation_id", conversation_id)
                .order("created_at", desc=False)
                .limit(limit)
                .execute()
            )
        res = await _run_sync(_query)
        return res.data or []
    except Exception:
        return []


async def keyword_search_kb(keywords: list[str], tunnel: str = "sales", limit: int = 3) -> list:
    """V1: full-text search. V2: Qdrant vector search.
    Searches both tunnel-specific AND universal ('all') entries.
    """
    try:
        db = get_client()
        query = " | ".join(keywords)
        def _query():
            return (
                db.table("kb_entries")
                .select("id,title,content,tunnel")
                .eq("is_active", True)
                .in_("tunnel", [tunnel, "all"])
                .text_search("search_vector", query)
                .execute()
            )
        res = await _run_sync(_query)
        return (res.data or [])[:limit]
    except Exception as e:
        logger.warning(f"keyword_search_kb error: {e}")
        return []


# ════════════════════════════════════════════════════════════════
# ADMIN — CONVERSATIONS
# ════════════════════════════════════════════════════════════════

async def get_conversations(
    tunnel: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    agent_id: Optional[str] = None,
    agent_id_is_null: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list, int]:
    """List conversations with filters. Returns (rows, total_count)."""
    try:
        db = get_client()
        def _query():
            q = db.table("conversations").select("*", count="exact").order("updated_at", desc=True)  # type: ignore[arg-type]
            if tunnel:  q = q.eq("tunnel", tunnel)
            if status:  q = q.eq("status", status)
            if agent_id:
                q = q.eq("assigned_agent_id", agent_id)
            elif agent_id_is_null:
                q = q.is_("assigned_agent_id", "null")
            if search:
                q = q.or_(
                    f"visitor_name.ilike.%{search}%,"
                    f"visitor_email.ilike.%{search}%,"
                    f"visitor_phone.ilike.%{search}%"
                )
            return q.range(offset, offset + limit - 1).execute()
        res = await _run_sync(_query)
        return res.data or [], res.count or 0
    except Exception as e:
        logger.error(f"get_conversations error: {e}")
        return [], 0


async def get_conversation(conversation_id: str) -> Optional[dict]:
    """One conversation + all its messages + associated lead."""
    try:
        db = get_client()
        conv = await _run_sync(
            lambda: db.table("conversations").select("*").eq("id", conversation_id).single().execute()
        )
        if not conv.data:
            return None
        msgs = await _run_sync(
            lambda: db.table("messages")
            .select("*")
            .eq("conversation_id", conversation_id)
            .order("created_at", desc=False)
            .execute()
        )
        # Fetch associated lead (if exists)
        lead_res = await _run_sync(
            lambda: db.table("leads")
            .select("*")
            .eq("conversation_id", conversation_id)
            .limit(1)
            .execute()
        )
        result = dict(conv.data)
        result["messages"] = msgs.data or []
        result["lead"] = lead_res.data[0] if lead_res.data else None
        return result
    except Exception as e:
        logger.error(f"get_conversation error: {e}")
        return None


async def get_conversation_mode(conversation_id: str) -> Optional[str]:
    """Get ONLY the mode of a conversation. Lightweight query for routing check."""
    try:
        db = get_client()
        res = await _run_sync(
            lambda: db.table("conversations")
            .select("mode")
            .eq("id", conversation_id)
            .single()
            .execute()
        )
        return res.data.get("mode") if res.data else None
    except Exception as e:
        logger.warning(f"get_conversation_mode error: {e}")
        return None


async def update_conversation(conversation_id: str, payload: dict) -> Optional[dict]:
    try:
        db = get_client()
        res = await _run_sync(lambda: db.table("conversations").update(payload).eq("id", conversation_id).execute())
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"update_conversation error: {e}")
        return None


# ════════════════════════════════════════════════════════════════
# ADMIN — LEADS
# ════════════════════════════════════════════════════════════════

async def get_leads(
    status: Optional[str] = None,
    tier: Optional[str] = None,
    tunnel: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list, int]:
    """List leads with JOIN on conversations for contact details. Returns (rows, total_count)."""
    try:
        db = get_client()
        def _query():
            q = db.table("leads").select(
                "*, conversations!inner(visitor_name, visitor_email, visitor_phone, tunnel)",
                count="exact"  # type: ignore[arg-type]
            ).order("score", desc=True)
            if status:  q = q.eq("status", status)
            if tier:    q = q.eq("tier", tier)
            if tunnel:  q = q.eq("conversations.tunnel", tunnel)
            if search:
                q = q.or_(
                    f"conversations.visitor_name.ilike.%{search}%,"
                    f"conversations.visitor_email.ilike.%{search}%,"
                    f"origin_code.ilike.%{search}%,"
                    f"destination_code.ilike.%{search}%"
                )
            return q.range(offset, offset + limit - 1).execute()
        res = await _run_sync(_query)
        rows = []
        for row in (res.data or []):
            flat = dict(row)
            conv = flat.pop("conversations", {}) or {}
            flat["visitor_name"]  = conv.get("visitor_name")
            flat["visitor_email"] = conv.get("visitor_email")
            flat["visitor_phone"] = conv.get("visitor_phone")
            rows.append(flat)
        return rows, res.count or 0
    except Exception as e:
        logger.error(f"get_leads error: {e}")
        return [], 0


async def get_lead_full(lead_id: str) -> Optional[dict]:
    """Full lead: lead + conversation contact info + route_segments."""
    try:
        db = get_client()
        lead_res = await _run_sync(
            lambda: db.table("leads")
            .select("*, conversations(visitor_name, visitor_email, visitor_phone, tunnel, metadata)")
            .eq("id", lead_id).single().execute()
        )
        if not lead_res.data:
            return None
        segs_res = await _run_sync(
            lambda: db.table("route_segments").select("*")
            .eq("lead_id", lead_id).order("segment_order", desc=False).execute()
        )
        result = dict(lead_res.data)
        conv = result.pop("conversations", {}) or {}
        result["visitor_name"]  = conv.get("visitor_name")
        result["visitor_email"] = conv.get("visitor_email")
        result["visitor_phone"] = conv.get("visitor_phone")
        result["route_segments"] = segs_res.data or []
        return result
    except Exception as e:
        logger.error(f"get_lead_full error: {e}")
        return None


async def update_lead(lead_id: str, payload: dict) -> Optional[dict]:
    """Update lead fields. Recalculates tier if score is modified."""
    try:
        db = get_client()
        if "score" in payload:
            s = payload["score"]
            payload["tier"] = "gold" if s >= 80 else "silver" if s >= 50 else "bronze"
        res = await _run_sync(lambda: db.table("leads").update(payload).eq("id", lead_id).execute())
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"update_lead error: {e}")
        return None


# ════════════════════════════════════════════════════════════════
# ADMIN — KNOWLEDGE BASE
# ════════════════════════════════════════════════════════════════

async def get_kb_categories(tunnel: Optional[str] = None) -> list:
    """KB categories with article count per category."""
    try:
        db = get_client()
        def _query():
            q = db.table("kb_categories").select("*, kb_entries(count)").order("sort_order")
            if tunnel:
                q = q.eq("tunnel", tunnel)
            return q.execute()
        res = await _run_sync(_query)
        result = []
        for row in (res.data or []):
            flat = dict(row)
            entries = flat.pop("kb_entries", []) or []
            flat["entry_count"] = entries[0].get("count", 0) if entries else 0
            result.append(flat)
        return result
    except Exception as e:
        logger.error(f"get_kb_categories error: {e}")
        return []


async def get_kb_entries(
    tunnel: Optional[str] = None,
    category_id: Optional[str] = None,
    is_active: Optional[bool] = None,
    limit: int = 100,
) -> list:
    try:
        db = get_client()
        def _query():
            q = db.table("kb_entries").select("*").order("updated_at", desc=True)
            if tunnel:      q = q.eq("tunnel", tunnel)
            if category_id: q = q.eq("category_id", category_id)
            if is_active is not None: q = q.eq("is_active", is_active)
            return q.limit(limit).execute()
        res = await _run_sync(_query)
        return res.data or []
    except Exception as e:
        logger.error(f"get_kb_entries error: {e}")
        return []


async def create_kb_entry(payload: dict) -> Optional[dict]:
    try:
        db = get_client()
        res = await _run_sync(lambda: db.table("kb_entries").insert(payload).execute())
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"create_kb_entry error: {e}")
        return None


async def update_kb_entry(entry_id: str, payload: dict) -> Optional[dict]:
    try:
        db = get_client()
        res = await _run_sync(lambda: db.table("kb_entries").update(payload).eq("id", entry_id).execute())
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"update_kb_entry error: {e}")
        return None


async def delete_kb_entry(entry_id: str) -> bool:
    try:
        db = get_client()
        await _run_sync(lambda: db.table("kb_entries").delete().eq("id", entry_id).execute())
        return True
    except Exception as e:
        logger.error(f"delete_kb_entry error: {e}")
        return False


# ════════════════════════════════════════════════════════════════
# ADMIN — USERS
# ════════════════════════════════════════════════════════════════

async def get_users(
    role: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list, int]:
    """List users with filters. Returns (rows, total_count)."""
    try:
        db = get_client()
        def _query():
            q = db.table("users").select("*", count="exact").order("created_at", desc=True)  # type: ignore[arg-type]
            if role:    q = q.eq("role", role)
            if search:
                q = q.or_(
                    f"name.ilike.%{search}%,"
                    f"email.ilike.%{search}%"
                )
            return q.range(offset, offset + limit - 1).execute()
        res = await _run_sync(_query)
        return res.data or [], res.count or 0
    except Exception as e:
        logger.error(f"get_users error: {e}")
        return [], 0


async def get_user_by_email(email: str) -> Optional[dict]:
    """Get single user by email for login."""
    try:
        db = get_client()
        res = await _run_sync(
            lambda: db.table("users").select("*").eq("email", email).single().execute()
        )
        return res.data if res.data else None
    except Exception as e:
        logger.error(f"get_user_by_email error: {e}")
        return None


async def create_user(payload: dict) -> Optional[dict]:
    """Create a new user (for invite flow)."""
    try:
        db = get_client()
        res = await _run_sync(lambda: db.table("users").insert(payload).execute())
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"create_user error: {e}")
        return None


async def update_user(user_id: str, payload: dict) -> Optional[dict]:
    try:
        db = get_client()
        res = await _run_sync(lambda: db.table("users").update(payload).eq("id", user_id).execute())
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"update_user error: {e}")
        return None


# ════════════════════════════════════════════════════════════════
# PIPELINE RUNS — recording
# ════════════════════════════════════════════════════════════════

async def create_pipeline_run(payload: dict) -> Optional[dict]:
    """Insert a pipeline run record. Non-fatal — never blocks the pipeline."""
    try:
        db_client = get_client()
        res = await _run_sync(lambda: db_client.table("pipeline_runs").insert(payload).execute())
        return res.data[0] if res.data else None
    except Exception as e:
        logger.warning(f"create_pipeline_run error (non-fatal): {e}")
        return None


async def get_today_cost() -> float:
    """Total AI cost today. One query, used by budget guard."""
    try:
        db = get_client()
        now = datetime.now(timezone.utc)
        today_str = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        res = await _run_sync(
            lambda: db.table("pipeline_runs")
            .select("cost")
            .gte("created_at", today_str)
            .execute()
        )
        return sum(float(r.get("cost", 0)) for r in (res.data or []))
    except Exception as e:
        logger.warning(f"get_today_cost error: {e}")
        return 0.0


# ════════════════════════════════════════════════════════════════
# ADMIN — DASHBOARD STATS
# ════════════════════════════════════════════════════════════════

async def get_dashboard_stats(tunnel_filter: Optional[str] = None) -> dict:
    """Dashboard statistics — all fields expected by frontend DashboardStats interface.
    Fetches bulk data via _run_sync, then processes in Python.
    If tunnel_filter is set, only rows matching that tunnel are included.
    """
    try:
        db = get_client()

        # ── Fetch conversations (optionally filtered by tunnel) ──
        convos_q = db.table("conversations").select(
            "id, tunnel, status, created_at, closed_at", count="exact"  # type: ignore[arg-type]
        )
        if tunnel_filter:
            convos_q = convos_q.eq("tunnel", tunnel_filter)
        convos = await _run_sync(lambda: convos_q.execute())
        all_convos = convos.data or []

        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday_start = today_start - timedelta(days=1)
        week_ago = now - timedelta(days=7)
        month_ago = now - timedelta(days=30)

        def parse_dt(s):
            if not s:
                return None
            try:
                return datetime.fromisoformat(s.replace("Z", "+00:00"))
            except Exception:
                return None

        conversations_today = sum(
            1 for c in all_convos
            if (dt := parse_dt(c.get("created_at"))) and dt >= today_start
        )
        conversations_yesterday = sum(
            1 for c in all_convos
            if (dt := parse_dt(c.get("created_at"))) and yesterday_start <= dt < today_start
        )
        conversations_week = sum(
            1 for c in all_convos
            if (dt := parse_dt(c.get("created_at"))) and dt >= week_ago
        )
        conversations_month = sum(
            1 for c in all_convos
            if (dt := parse_dt(c.get("created_at"))) and dt >= month_ago
        )
        conversations_active = sum(
            1 for c in all_convos if c.get("status") == "active"
        )

        # ── Fetch leads (optionally filtered by tunnel via conversations join) ──
        if tunnel_filter:
            leads_res = await _run_sync(lambda: db.table("leads").select(
                "id, score, tier, status, origin_code, destination_code, "
                "route_display, created_at, conversation_id, "
                "conversations!inner(tunnel)"
            ).eq("conversations.tunnel", tunnel_filter).execute())
            all_leads = leads_res.data or []
            # Strip the nested join object so downstream code isn't affected
            for lead in all_leads:
                lead.pop("conversations", None)
        else:
            leads_res = await _run_sync(lambda: db.table("leads").select(
                "id, score, tier, status, origin_code, destination_code, "
                "route_display, created_at, conversation_id"
            ).execute())
            all_leads = leads_res.data or []

        leads_total = len(all_leads)
        leads_new = sum(1 for l in all_leads if l.get("status") == "new")
        leads_contacted = sum(1 for l in all_leads if l.get("status") == "contacted")
        leads_qualified = sum(1 for l in all_leads if l.get("status") == "qualified")
        leads_converted = sum(1 for l in all_leads if l.get("status") == "converted")
        leads_lost = sum(1 for l in all_leads if l.get("status") == "lost")
        leads_gold = sum(1 for l in all_leads if l.get("tier") == "gold")
        leads_silver = sum(1 for l in all_leads if l.get("tier") == "silver")
        leads_bronze = sum(1 for l in all_leads if l.get("tier") == "bronze")
        leads_uncalled = leads_new

        # SLA breach — new leads older than 2 hours
        sla_cutoff = now - timedelta(hours=2)
        leads_sla_breach = sum(
            1 for l in all_leads
            if l.get("status") == "new" and (dt := parse_dt(l.get("created_at"))) and dt < sla_cutoff
        )

        # ── Pipeline runs (cost, latency, fallback) ──────────
        pipeline_q = db.table("pipeline_runs").select(
            "cost, latency_ms, status, had_fallback, tunnel, created_at"
        )
        if tunnel_filter:
            pipeline_q = pipeline_q.eq("tunnel", tunnel_filter)
        pipeline_res = await _run_sync(lambda: pipeline_q.execute())
        all_runs = pipeline_res.data or []

        cost_today = sum(
            float(r.get("cost", 0)) for r in all_runs
            if (dt := parse_dt(r.get("created_at"))) and dt >= today_start
        )
        cost_week = sum(
            float(r.get("cost", 0)) for r in all_runs
            if (dt := parse_dt(r.get("created_at"))) and dt >= week_ago
        )
        cost_month = sum(
            float(r.get("cost", 0)) for r in all_runs
            if (dt := parse_dt(r.get("created_at"))) and dt >= month_ago
        )

        latencies = [r["latency_ms"] for r in all_runs if r.get("latency_ms")]
        latency_median = round(statistics.median(latencies)) if latencies else 0

        total_runs = len(all_runs)
        fallback_count = sum(1 for r in all_runs if r.get("had_fallback") or r.get("status") == "fallback")
        fallback_rate = (fallback_count / total_runs * 100) if total_runs > 0 else 0

        daily_budget = settings.daily_budget
        cost_vs_budget = (cost_today / daily_budget * 100) if daily_budget > 0 else 0

        # ── avg_duration_minutes — from closed conversations ──
        durations = []
        for c in all_convos:
            if c.get("status") == "closed" and c.get("closed_at"):
                start = parse_dt(c.get("created_at"))
                end = parse_dt(c.get("closed_at"))
                if start and end:
                    durations.append((end - start).total_seconds() / 60)
        avg_duration_minutes = round(statistics.mean(durations), 1) if durations else 0.0

        # ── Messages total month ─────────────────────────────
        msgs_res = await _run_sync(lambda: db.table("messages").select("id", count="exact").execute())  # type: ignore[arg-type]
        messages_total_month = msgs_res.count or 0

        # ── Top routes ────────────────────────────────────────
        route_counter: Counter = Counter()
        for l in all_leads:
            origin = l.get("origin_code", "")
            dest = l.get("destination_code", "")
            if origin and dest:
                route_counter[f"{origin} → {dest}"] += 1
        top_routes = [{"route": r, "count": c} for r, c in route_counter.most_common(5)]

        # ── Conversations trend (14 days) ─────────────────────
        conversations_trend = []
        conversations_trend_v2 = []
        for i in range(13, -1, -1):
            day = today_start - timedelta(days=i)
            day_str = day.strftime("%Y-%m-%d")
            day_convos = [
                c for c in all_convos
                if (dt := parse_dt(c.get("created_at"))) and dt.date() == day.date()
            ]
            conversations_trend.append({"date": day_str, "count": len(day_convos)})
            sales = sum(1 for c in day_convos if c.get("tunnel") == "sales")
            support = sum(1 for c in day_convos if c.get("tunnel") == "support")
            conversations_trend_v2.append({"date": day_str, "sales": sales, "support": support})

        # ── Leads trend (14 days) ─────────────────────────────
        leads_trend = []
        for i in range(13, -1, -1):
            day = today_start - timedelta(days=i)
            day_str = day.strftime("%Y-%m-%d")
            day_leads = [
                l for l in all_leads
                if (dt := parse_dt(l.get("created_at"))) and dt.date() == day.date()
            ]
            leads_trend.append({"date": day_str, "count": len(day_leads)})

        # ── Leads sparkline 7d ────────────────────────────────
        leads_sparkline_7d = []
        for i in range(6, -1, -1):
            day = today_start - timedelta(days=i)
            count = sum(
                1 for l in all_leads
                if (dt := parse_dt(l.get("created_at"))) and dt.date() == day.date()
            )
            leads_sparkline_7d.append(count)

        # ── Hot leads (top 5 by score, new status) ────────────
        hot_leads = []
        new_leads_sorted = sorted(
            [l for l in all_leads if l.get("status") == "new"],
            key=lambda x: x.get("score", 0), reverse=True
        )[:5]
        for l in new_leads_sorted:
            created = parse_dt(l.get("created_at"))
            minutes_since = int((now - created).total_seconds() / 60) if created else 0
            cid = l.get("conversation_id", "")
            conv_res = await _run_sync(
                lambda: db.table("conversations").select("visitor_name").eq("id", cid).limit(1).execute()
            )
            visitor_name = conv_res.data[0].get("visitor_name") if conv_res.data else None
            hot_leads.append({
                "id": l["id"],
                "visitor_name": visitor_name,
                "route": l.get("route_display") or f"{l.get('origin_code', '?')} → {l.get('destination_code', '?')}",
                "score": l.get("score", 0),
                "tier": l.get("tier", "bronze"),
                "minutes_since_created": minutes_since,
            })

        # ── Funnel ────────────────────────────────────────────
        funnel = [
            {"name": "New", "count": leads_new, "color": "#3b82f6"},
            {"name": "Contacted", "count": leads_contacted, "color": "#f59e0b"},
            {"name": "Qualified", "count": leads_qualified, "color": "#8b5cf6"},
            {"name": "Converted", "count": leads_converted, "color": "#22c55e"},
            {"name": "Lost", "count": leads_lost, "color": "#ef4444"},
        ]

        return {
            "conversations_today": conversations_today,
            "conversations_yesterday": conversations_yesterday,
            "conversations_week": conversations_week,
            "conversations_month": conversations_month,
            "conversations_active": conversations_active,
            "leads_total": leads_total,
            "leads_new": leads_new,
            "leads_contacted": leads_contacted,
            "leads_qualified": leads_qualified,
            "leads_converted": leads_converted,
            "leads_lost": leads_lost,
            "leads_gold": leads_gold,
            "leads_silver": leads_silver,
            "leads_bronze": leads_bronze,
            "cost_today": round(cost_today, 4),
            "cost_week": round(cost_week, 4),
            "cost_month": round(cost_month, 4),
            "top_routes": top_routes,
            "conversations_trend": conversations_trend,
            "leads_trend": leads_trend,
            "leads_uncalled": leads_uncalled,
            "leads_sla_breach": leads_sla_breach,
            "cost_avg_30d": round(cost_month / 30, 4) if cost_month else 0,
            "daily_budget": daily_budget,
            "latency_median_ms": latency_median,
            "fallback_rate_percent": round(fallback_rate, 1),
            "cost_vs_budget_percent": round(cost_vs_budget, 1),
            "avg_duration_minutes": avg_duration_minutes,
            "messages_total_month": messages_total_month,
            "conversations_trend_v2": conversations_trend_v2,
            "hot_leads": hot_leads,
            "leads_sparkline_7d": leads_sparkline_7d,
            "funnel": funnel,
        }
    except Exception as e:
        logger.error(f"get_dashboard_stats error: {e}")
        return {
            "conversations_today": 0, "conversations_yesterday": 0,
            "conversations_week": 0, "conversations_month": 0, "conversations_active": 0,
            "leads_total": 0, "leads_new": 0, "leads_contacted": 0,
            "leads_qualified": 0, "leads_converted": 0, "leads_lost": 0,
            "leads_gold": 0, "leads_silver": 0, "leads_bronze": 0,
            "cost_today": 0, "cost_week": 0, "cost_month": 0,
            "top_routes": [], "conversations_trend": [], "leads_trend": [],
            "leads_uncalled": 0, "leads_sla_breach": 0,
            "cost_avg_30d": 0, "daily_budget": 50.0,
            "latency_median_ms": 0, "fallback_rate_percent": 0,
            "cost_vs_budget_percent": 0, "avg_duration_minutes": 0,
            "messages_total_month": 0,
            "conversations_trend_v2": [], "hot_leads": [],
            "leads_sparkline_7d": [0, 0, 0, 0, 0, 0, 0], "funnel": [],
        }
