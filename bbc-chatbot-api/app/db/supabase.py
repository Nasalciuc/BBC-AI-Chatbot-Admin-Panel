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
    visitor_id: Optional[str] = None,
) -> Optional[dict]:
    """Return existing conversation or create a new one.

    Dual-path matching:
      Path A — conversation_id supplied & passes fingerprint check → return it.
      Path B — visitor_id supplied → find newest active conv for this visitor.
      Fallback — create new conversation.
    """
    try:
        db = get_client()

        # Defence-in-depth: reject non-UUID conversation_id early so the
        # Supabase query never sends an invalid value (avoids 22P02).
        if conversation_id is not None:
            import uuid as _uuid
            try:
                _uuid.UUID(conversation_id)
            except (ValueError, AttributeError, TypeError):
                logger.warning(
                    f"get_or_create_conversation: invalid conversation_id "
                    f"{conversation_id!r}, treating as new conversation"
                )
                conversation_id = None

        # ── Path A: conversation_id supplied ──────────────────────
        if conversation_id:
            res = await _run_sync(lambda: db.table("conversations").select("*").eq("id", conversation_id).single().execute())
            if res.data:
                existing = res.data

                req_email = (getattr(visitor, "email", None) or "").strip().lower()
                req_phone = (getattr(visitor, "phone", None) or "").strip()
                req_name = (getattr(visitor, "name", None) or "").strip()

                ex_email = (existing.get("visitor_email") or "").strip().lower()
                ex_phone = (existing.get("visitor_phone") or "").strip()
                ex_name = (existing.get("visitor_name") or "").strip()

                has_req_identity = bool(req_email or req_phone or req_name)
                has_ex_identity = bool(ex_email or ex_phone or ex_name)

                mismatch = False
                if req_email and ex_email and req_email != ex_email:
                    mismatch = True
                if req_phone and ex_phone and req_phone != ex_phone:
                    mismatch = True
                if not has_req_identity and has_ex_identity:
                    mismatch = True

                if not mismatch:
                    # Back-fill visitor_id if missing on existing row
                    if visitor_id and not existing.get("visitor_id"):
                        await _run_sync(
                            lambda: db.table("conversations")
                            .update({"visitor_id": visitor_id})
                            .eq("id", conversation_id)
                            .execute()
                        )
                    return existing

                logger.warning(
                    "get_or_create_conversation: rejected stale/mismatched conversation_id "
                    f"{conversation_id} (tunnel={tunnel})"
                )

        # ── Path B: visitor_id supplied — find active conv ────────
        if visitor_id:
            res = await _run_sync(
                lambda: db.table("conversations")
                .select("*")
                .eq("visitor_id", visitor_id)
                .eq("status", "active")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if res.data:
                return res.data[0]

        # ── Fallback: create new conversation ─────────────────────
        payload: dict = {"tunnel": tunnel, "mode": "ai", "status": "active"}
        if visitor_id:
            payload["visitor_id"] = visitor_id
        if visitor and visitor.name:  payload["visitor_name"]  = visitor.name
        if visitor and visitor.email: payload["visitor_email"] = visitor.email
        if visitor and visitor.phone:
            from app.services.crm import format_phone_international
            payload["visitor_phone"] = format_phone_international(visitor.phone)
        if visitor and visitor.country_code: payload["visitor_phone_country"] = visitor.country_code
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
                .order("created_at", desc=True)
                .limit(limit)
                .execute()
            )
        res = await _run_sync(_query)
        return list(reversed(res.data)) if res.data else []
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


async def get_conversation_simple(conv_id: str) -> Optional[dict]:
    """Get minimal conversation info — status and mode only. Fast check."""
    try:
        db = get_client()
        res = await _run_sync(
            lambda: db.table("conversations")
            .select("id, tunnel, status, mode, assigned_agent_id, visitor_id, metadata, updated_at")
            .eq("id", conv_id)
            .single()
            .execute()
        )
        return res.data
    except Exception as e:
        logger.error(f"get_conversation_simple error: {e}")
        return None


async def get_last_agent_for_visitor(
    email: Optional[str],
    phone: Optional[str],
) -> Optional[str]:
    """Get assigned_agent_id from visitor's most recent conversation.
    Used for returning visitor routing — route back to same operator.
    Searches by email OR phone. Returns None if no previous agent found."""
    email_clean = email.lower().strip() if email else None
    phone_clean = phone.strip() if phone else None
    if not email_clean and not phone_clean:
        return None
    try:
        db_client = get_client()

        def _q():
            q = (
                db_client.table("conversations")
                .select("assigned_agent_id")
                .not_.is_("assigned_agent_id", "null")
                .order("created_at", desc=True)
                .limit(1)
            )
            if email_clean and phone_clean:
                q = q.or_(
                    f"visitor_email.eq.{email_clean},"
                    f"visitor_phone.eq.{phone_clean}"
                )
            elif email_clean:
                q = q.eq("visitor_email", email_clean)
            else:
                q = q.eq("visitor_phone", phone_clean)
            return q.execute()

        res = await _run_sync(_q)
        if res and res.data:
            return res.data[0].get("assigned_agent_id")
        return None
    except Exception as e:
        logger.error(f"get_last_agent_for_visitor error: {e}")
        return None

async def get_conversation(conversation_id: str) -> Optional[dict]:
    """One conversation + all its messages + associated lead.
    Messages and lead queries run in PARALLEL (don't depend on each other)."""
    try:
        import asyncio
        db_client = get_client()
        conv = await _run_sync(
            lambda: db_client.table("conversations").select("*, assigned_agent:users!conversations_assigned_agent_id_fkey(name, email)").eq("id", conversation_id).single().execute()
        )
        if not conv.data:
            return None

        # Run msgs + lead in PARALLEL — both only need conversation_id
        msgs_future = _run_sync(
            lambda: db_client.table("messages")
            .select("*")
            .eq("conversation_id", conversation_id)
            .order("created_at", desc=False)
            .execute()
        )
        lead_future = _run_sync(
            lambda: db_client.table("leads")
            .select("*")
            .eq("conversation_id", conversation_id)
            .limit(1)
            .execute()
        )
        msgs, lead_res = await asyncio.gather(msgs_future, lead_future)

        result = dict(conv.data)
        # Flatten nested agent data into top-level field
        agent_data = result.pop("assigned_agent", None)
        result["assigned_agent_name"] = (
            agent_data.get("name") or agent_data.get("email")
            if agent_data else None
        )
        result["messages"] = msgs.data or []
        result["lead"] = lead_res.data[0] if lead_res.data else None
        return result
    except Exception as e:
        logger.error(f"get_conversation error: {e}")
        return None


async def get_conversation_counts(
    agent_id: str,
    tunnel: Optional[str] = None,
) -> dict:
    """Lightweight counts for queue tabs. 3 fast count queries, zero row data."""
    try:
        db_client = get_client()

        async def _count(agent_filter: str, status_val: str) -> int:
            def _q():
                q = db_client.table("conversations").select("id", count="exact")  # type: ignore[arg-type]
                if tunnel:
                    q = q.eq("tunnel", tunnel)
                q = q.eq("status", status_val)
                if agent_filter == "me":
                    q = q.eq("assigned_agent_id", agent_id)
                elif agent_filter == "none":
                    q = q.is_("assigned_agent_id", "null")
                return q.limit(0).execute()
            res = await _run_sync(_q)
            return res.count or 0

        my_active = await _count("me", "active")
        queue_count = await _count("none", "active")
        my_closed = await _count("me", "closed")

        async def _count_all(status_val: str) -> int:
            def _q():
                q = db_client.table("conversations").select("id", count="exact")  # type: ignore[arg-type]
                if tunnel:
                    q = q.eq("tunnel", tunnel)
                q = q.eq("status", status_val)
                return q.limit(0).execute()
            res = await _run_sync(_q)
            return res.count or 0

        all_active = await _count_all("active")
        all_closed = await _count_all("closed")

        return {
            "my_active": my_active,
            "queue": queue_count,
            "my_closed": my_closed,
            "all_active": all_active,
            "all_closed": all_closed,
        }
    except Exception as e:
        logger.error(f"get_conversation_counts error: {e}")
        return {"my_active": 0, "queue": 0, "my_closed": 0, "all_active": 0, "all_closed": 0}


async def get_messages_after(
    conversation_id: str,
    after: Optional[str] = None,
) -> list:
    """Get messages, optionally only those created after a timestamp.
    When 'after' is provided, returns only NEW messages (incremental polling).
    When 'after' is None, returns ALL messages (initial load)."""
    try:
        db_client = get_client()
        def _q():
            q = db_client.table("messages").select("*") \
                .eq("conversation_id", conversation_id) \
                .order("created_at", desc=False)
            if after:
                q = q.gt("created_at", after)
            return q.execute()
        res = await _run_sync(_q)
        return res.data or []
    except Exception as e:
        logger.error(f"get_messages_after error: {e}")
        return []


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
    include_drafts: bool = False,
) -> tuple[list, int]:
    """List leads with JOIN on conversations for contact details. Returns (rows, total_count)."""
    try:
        db = get_client()
        def _query():
            q = db.table("leads").select(
                "*, conversations!inner(visitor_name, visitor_email, visitor_phone, tunnel)",
                count="exact"  # type: ignore[arg-type]
            ).order("score", desc=True)
            if not include_drafts:
                q = q.eq("created_in_crm", True)
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


async def get_existing_lead_by_contact(
    email: Optional[str] = None,
    phone: Optional[str] = None,
) -> Optional[dict]:
    """Check if a lead exists by email or phone (CRM duplicate check).
    Returns the lead with conversation contact info and assigned agent if found.
    Used for: Routing returning visitors to same agent, CRM integration checks."""
    if not email and not phone:
        return None
    email_clean = email.lower().strip() if email else None
    phone_clean = phone.strip() if phone else None
    try:
        db_client = get_client()
        def _q():
            q = (
                db_client.table("leads")
                .select("*, conversations(id, visitor_name, visitor_email, visitor_phone, assigned_agent_id, tunnel)")
                .order("created_at", desc=True)
                .limit(1)
            )
            # Search by email OR phone in conversations
            if email_clean and phone_clean:
                q = q.or_(
                    f"conversations.visitor_email.eq.{email_clean},"
                    f"conversations.visitor_phone.eq.{phone_clean}"
                )
            elif email_clean:
                q = q.eq("conversations.visitor_email", email_clean)
            else:
                q = q.eq("conversations.visitor_phone", phone_clean)
            return q.execute()
        res = await _run_sync(_q)
        if res and res.data:
            lead = dict(res.data[0])
            conv = lead.pop("conversations", {}) or {}
            lead["visitor_name"] = conv.get("visitor_name")
            lead["visitor_email"] = conv.get("visitor_email")
            lead["visitor_phone"] = conv.get("visitor_phone")
            lead["assigned_agent_id"] = conv.get("assigned_agent_id")
            lead["conversation_id"] = conv.get("id")
            lead["tunnel"] = conv.get("tunnel")
            return lead
        return None
    except Exception as e:
        logger.error(f"get_existing_lead_by_contact error: {e}")
        return None


async def mark_lead_created_in_crm(lead_id: str) -> Optional[dict]:
    """Mark a lead as successfully created in the CRM system.
    Updates: created_in_crm=true, created_in_crm_at=NOW()"""
    try:
        db_client = get_client()
        from datetime import datetime
        payload = {
            "created_in_crm": True,
            "created_in_crm_at": datetime.utcnow().isoformat(),
        }
        res = await _run_sync(
            lambda: db_client.table("leads").update(payload).eq("id", lead_id).execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"mark_lead_created_in_crm error: {e}")
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
    db = get_client()
    def _query():
        q = db.table("users").select(
            "id,email,name,role,tunnel_scope,avatar_url,is_active,last_seen_at,phone,created_at,updated_at",
            count="exact",  # type: ignore[arg-type]
        ).order("created_at", desc=True)
        if role:    q = q.eq("role", role)
        if search:
            q = q.or_(
                f"name.ilike.%{search}%,"
                f"email.ilike.%{search}%"
            )
        return q.range(offset, offset + limit - 1).execute()
    res = await _run_sync(_query)
    return res.data or [], res.count or 0


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


async def get_user_by_id(user_id: str) -> Optional[dict]:
    """Get single user by id."""
    try:
        db = get_client()
        res = await _run_sync(
            lambda: db.table("users").select("*").eq("id", user_id).single().execute()
        )
        return res.data if res.data else None
    except Exception as e:
        logger.error(f"get_user_by_id error: {e}")
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


async def create_user_access_audit(payload: dict) -> Optional[dict]:
    """Record access rights change for a user."""
    try:
        db = get_client()
        res = await _run_sync(lambda: db.table("user_access_audit").insert(payload).execute())
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"create_user_access_audit error: {e}")
        return None


async def get_user_access_audit(target_user_id: str, limit: int = 50) -> list:
    """Get recent access-rights history for a specific user."""
    try:
        db = get_client()
        res = await _run_sync(
            lambda: db.table("user_access_audit")
            .select("*, actor:users!changed_by_user_id(name,email)")
            .eq("target_user_id", target_user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows = []
        for row in (res.data or []):
            flat = dict(row)
            actor = flat.pop("actor", {}) or {}
            flat["changed_by_name"] = actor.get("name")
            flat["changed_by_email"] = actor.get("email")
            rows.append(flat)
        return rows
    except Exception as e:
        logger.error(f"get_user_access_audit error: {e}")
        return []


async def create_invite_token(payload: dict) -> Optional[dict]:
    """Create one-time invite token row."""
    try:
        db = get_client()
        res = await _run_sync(lambda: db.table("invite_tokens").insert(payload).execute())
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"create_invite_token error: {e}")
        return None


async def invalidate_active_invite_tokens(user_id: str, purpose: str = "set_password") -> int:
    """Invalidate previous unused tokens for a user (single active link policy)."""
    try:
        db = get_client()
        now_iso = datetime.now(timezone.utc).isoformat()
        res = await _run_sync(
            lambda: db.table("invite_tokens")
            .update({"used_at": now_iso})
            .eq("user_id", user_id)
            .eq("purpose", purpose)
            .is_("used_at", "null")
            .execute()
        )
        return len(res.data or [])
    except Exception as e:
        logger.error(f"invalidate_active_invite_tokens error: {e}")
        return 0


async def get_valid_invite_token(token: str, purpose: str = "set_password") -> Optional[dict]:
    """Return invite token row only if unused and not expired."""
    try:
        db = get_client()
        now_iso = datetime.now(timezone.utc).isoformat()
        res = await _run_sync(
            lambda: db.table("invite_tokens")
            .select("*")
            .eq("token", token)
            .eq("purpose", purpose)
            .is_("used_at", "null")
            .gt("expires_at", now_iso)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"get_valid_invite_token error: {e}")
        return None


async def mark_invite_token_used(token: str) -> Optional[dict]:
    """Mark token as consumed (first successful use)."""
    try:
        db = get_client()
        now_iso = datetime.now(timezone.utc).isoformat()
        res = await _run_sync(
            lambda: db.table("invite_tokens")
            .update({"used_at": now_iso})
            .eq("token", token)
            .is_("used_at", "null")
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"mark_invite_token_used error: {e}")
        return None


async def consume_valid_invite_token(token: str, purpose: str = "set_password") -> Optional[dict]:
    """Atomically consume invite token if still unused and not expired."""
    try:
        db = get_client()
        now_iso = datetime.now(timezone.utc).isoformat()
        res = await _run_sync(
            lambda: db.table("invite_tokens")
            .update({"used_at": now_iso})
            .eq("token", token)
            .eq("purpose", purpose)
            .is_("used_at", "null")
            .gt("expires_at", now_iso)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"consume_valid_invite_token error: {e}")
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
    Fetches bulk data via parallel _run_sync calls, then processes in Python.
    If tunnel_filter is set, only rows matching that tunnel are included.
    """
    try:
        db = get_client()

        # ── Parallel fetch: conversations, leads, pipeline_runs, messages count ──
        # NOTE: Supabase default limit = 1000 rows; use .limit(10000) to fetch all
        convos_q = db.table("conversations").select(
            "id, tunnel, status, visitor_name, created_at, closed_at"
        ).limit(10000)
        if tunnel_filter:
            convos_q = convos_q.eq("tunnel", tunnel_filter)

        if tunnel_filter:
            leads_future = _run_sync(lambda: db.table("leads").select(
                "id, score, tier, status, origin_code, destination_code, "
                "route_display, created_at, conversation_id, "
                "conversations!inner(tunnel)"
            ).eq("conversations.tunnel", tunnel_filter).limit(10000).execute())
        else:
            leads_future = _run_sync(lambda: db.table("leads").select(
                "id, score, tier, status, origin_code, destination_code, "
                "route_display, created_at, conversation_id"
            ).limit(10000).execute())

        pipeline_q = db.table("pipeline_runs").select(
            "cost, latency_ms, status, had_fallback, tunnel, created_at"
        ).limit(10000)
        if tunnel_filter:
            pipeline_q = pipeline_q.eq("tunnel", tunnel_filter)

        # Fire all 4 queries in parallel
        convos, leads_res, pipeline_res, msgs_res = await asyncio.gather(
            _run_sync(lambda: convos_q.execute()),
            leads_future,
            _run_sync(lambda: pipeline_q.execute()),
            _run_sync(lambda: db.table("messages").select("id", count="exact").limit(0).execute()),  # type: ignore[arg-type]
        )

        all_convos = convos.data or []
        all_leads = leads_res.data or []
        if tunnel_filter:
            for lead in all_leads:
                lead.pop("conversations", None)
        all_runs = pipeline_res.data or []
        messages_total_month = msgs_res.count or 0

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

        # ── Leads aggregates ──
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

        sla_cutoff = now - timedelta(hours=2)
        leads_sla_breach = sum(
            1 for l in all_leads
            if l.get("status") == "new" and (dt := parse_dt(l.get("created_at"))) and dt < sla_cutoff
        )

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

        # ── Messages total month (already fetched in parallel) ─
        # messages_total_month set above from parallel gather

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
        # Build conv_id→visitor_name lookup from already-fetched conversations (no N+1)
        conv_name_map = {c["id"]: c.get("visitor_name") for c in all_convos}
        hot_leads = []
        new_leads_sorted = sorted(
            [l for l in all_leads if l.get("status") == "new"],
            key=lambda x: x.get("score", 0), reverse=True
        )[:5]
        for l in new_leads_sorted:
            created = parse_dt(l.get("created_at"))
            minutes_since = int((now - created).total_seconds() / 60) if created else 0
            hot_leads.append({
                "id": l["id"],
                "visitor_name": conv_name_map.get(l.get("conversation_id", ""), None),
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


# ════════════════════════════════════════════════════════════════
# AGENT PRESENCE & ROUTING
# ════════════════════════════════════════════════════════════════


async def update_user_last_seen(user_id: str) -> None:
    """Update agent's last_seen_at timestamp (heartbeat)."""
    try:
        db_client = get_client()
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        await _run_sync(
            lambda: db_client.table("users")
            .update({"last_seen_at": now_iso})
            .eq("id", user_id)
            .execute()
        )
    except Exception as e:
        logger.warning(f"update_user_last_seen error: {e}")


# Roles excluded from chat distribution (auto-assignment, sticky routing, stale cleanup).
# These users manage/observe but never handle visitor conversations directly.
# If adding a new role, decide: does this role HANDLE chats? If NO → add here.
_MANAGEMENT_ROLES = ("owner", "admin", "dev", "supervisor")


async def get_available_agents(tunnel: str, timeout_seconds: int = 120) -> list:
    """Get agents online (heartbeat within timeout) matching tunnel scope.
    Excludes management roles (owner/admin/dev) — they are not operators."""
    try:
        db_client = get_client()
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)).isoformat()

        def _q():
            return (
                db_client.table("users")
                .select("id, name, email, role, tunnel_scope, last_seen_at, chats_served_today, chats_served_date")
                .eq("is_active", True)
                .gt("last_seen_at", cutoff)
                .or_(f"tunnel_scope.eq.{tunnel},tunnel_scope.eq.all")
                .not_.in_("role", list(_MANAGEMENT_ROLES))
                .eq("is_ready", True)
                .execute()
            )
        res = await _run_sync(_q)
        return res.data or []
    except Exception as e:
        logger.error(f"get_available_agents error: {e}")
        return []


async def get_agent_active_count(agent_id: str) -> int:
    """Count active conversations assigned to an agent."""
    try:
        db_client = get_client()
        res = await _run_sync(
            lambda: db_client.table("conversations")
            .select("id", count="exact")  # type: ignore[arg-type]
            .eq("assigned_agent_id", agent_id)
            .eq("status", "active")
            .limit(0)
            .execute()
        )
        return res.count or 0
    except Exception:
        return 0


async def get_stale_agent_conversations(timeout_seconds: int) -> list:
    """Get active human-mode conversations assigned to agents who went offline.
    Uses !inner join on users table to filter by last_seen_at."""
    try:
        db_client = get_client()
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)).isoformat()
        res = await _run_sync(
            lambda: db_client.table("conversations")
            .select("id, assigned_agent_id, users!conversations_assigned_agent_id_fkey!inner(last_seen_at)")
            .eq("status", "active")
            .eq("mode", "human")
            .not_.is_("assigned_agent_id", "null")
            .lt("users.last_seen_at", cutoff)
            .execute()
        )
        return res.data or []
    except Exception as e:
        logger.error(f"get_stale_agent_conversations error: {e}")
        return []


async def get_last_agent_message_time(conversation_id: str):
    """Get timestamp of the most recent agent message in a conversation.
    Returns datetime or None."""
    try:
        db_client = get_client()
        res = await _run_sync(
            lambda: db_client.table("messages")
            .select("created_at")
            .eq("conversation_id", conversation_id)
            .eq("role", "agent")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if res.data and res.data[0].get("created_at"):
            from datetime import datetime
            raw = res.data[0]["created_at"]
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return None
    except Exception as e:
        logger.error(f"get_last_agent_message_time error: {e}")
        return None


async def get_all_agents_status(timeout_seconds: int = 120) -> list:
    """All agents with computed online/offline status."""
    try:
        db_client = get_client()
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)).isoformat()
        res = await _run_sync(
            lambda: db_client.table("users")
            .select("id, name, email, role, tunnel_scope, is_active, last_seen_at")
            .in_("role", ["sales", "support", "admin", "owner"])
            .execute()
        )
        agents = res.data or []
        for a in agents:
            a["is_online"] = bool(
                a.get("is_active")
                and a.get("last_seen_at")
                and a["last_seen_at"] > cutoff
            )
        return agents
    except Exception as e:
        logger.error(f"get_all_agents_status error: {e}")
        return []



async def increment_chats_served(agent: dict) -> None:
    """Increment daily chat counter. Uses data already in agent dict — 1 DB call.
    Lazy reset: if date changed, resets to 1 instead of incrementing."""
    try:
        from datetime import date
        db_client = get_client()
        today = date.today().isoformat()
        served_date = agent.get("chats_served_date") or ""
        current = agent.get("chats_served_today", 0) if served_date == today else 0
        new_count = current + 1
        await _run_sync(
            lambda: db_client.table("users")
            .update({"chats_served_today": new_count, "chats_served_date": today})
            .eq("id", agent["id"])
            .execute()
        )
    except Exception as e:
        logger.warning(f"increment_chats_served error (non-blocking): {e}")


async def get_oldest_unassigned_conversation(tunnel: str) -> dict | None:
    """Get oldest active AI conversation with no assigned agent.
    Includes needs_agent status to prioritize explicit agent requests."""
    try:
        db_client = get_client()
        res = await _run_sync(
            lambda: db_client.table("conversations")
            .select("id, tunnel, mode, status, created_at")
            .in_("status", ["active", "needs_agent"])
            .eq("mode", "ai")
            .is_("assigned_agent_id", "null")
            .eq("tunnel", tunnel)
            .order("created_at", desc=False)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception as e:
        logger.error(f"get_oldest_unassigned_conversation error: {e}")
        return None


# ════════════════════════════════════════════════════════════════
# TASKS
# ════════════════════════════════════════════════════════════════

TASK_SELECT = (
    "id, task_number, title, description, status, label, priority, "
    "assignee_id, created_by, due_date, created_at, updated_at"
)


async def get_tasks(
    status: Optional[str] = None,
    priority: Optional[str] = None,
    assignee_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list, int]:
    """List tasks with optional filters. Returns (rows, total)."""
    db_client = get_client()

    def _query():
        q = db_client.table("tasks").select(TASK_SELECT, count="exact").order("created_at", desc=True)  # type: ignore[arg-type]
        if status:
            q = q.eq("status", status)
        if priority:
            q = q.eq("priority", priority)
        if assignee_id:
            q = q.eq("assignee_id", assignee_id)
        return q.range(offset, offset + limit - 1).execute()

    res = await _run_sync(_query)
    return res.data or [], res.count or 0


async def get_task(task_id: str) -> Optional[dict]:
    """Get a single task by ID."""
    db_client = get_client()
    res = await _run_sync(
        lambda: db_client.table("tasks").select(TASK_SELECT).eq("id", task_id).single().execute()
    )
    return res.data if res.data else None


async def create_task(payload: dict) -> Optional[dict]:
    """Create a new task."""
    db_client = get_client()
    res = await _run_sync(
        lambda: db_client.table("tasks").insert(payload).execute()
    )
    return res.data[0] if res.data else None


async def update_task(task_id: str, payload: dict) -> Optional[dict]:
    """Update a task by ID."""
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    db_client = get_client()
    res = await _run_sync(
        lambda: db_client.table("tasks").update(payload).eq("id", task_id).execute()
    )
    return res.data[0] if res.data else None


async def delete_task(task_id: str) -> bool:
    """Delete a task by ID."""
    db_client = get_client()
    res = await _run_sync(
        lambda: db_client.table("tasks").delete().eq("id", task_id).execute()
    )
    return bool(res.data)


# ════════════════════════════════════════════════════════════════
# NOTIFICATIONS
# ════════════════════════════════════════════════════════════════

async def get_pending_conversations(agent_id: Optional[str] = None) -> list[dict]:
    """Get conversations waiting for agent response > 5 minutes.
    If agent_id provided → only that agent's conversations.
    If None (owner/admin) → all stale conversations."""
    try:
        db_client = get_client()

        def _q():
            q = (
                db_client.table("conversations")
                .select("id, visitor_name, tunnel, assigned_agent_id, created_at, messages(role, created_at)")
                .eq("status", "active")
                .eq("mode", "human")
                .not_.is_("assigned_agent_id", "null")
            )
            if agent_id:
                q = q.eq("assigned_agent_id", agent_id)
            return q.execute()

        res = await _run_sync(_q)
        conversations = res.data or []

        stale = []
        now = datetime.now(timezone.utc)

        for conv in conversations:
            messages = conv.get("messages", [])
            client_msgs = [m for m in messages if m.get("role") == "user"]
            agent_msgs = [m for m in messages if m.get("role") == "agent"]

            if not client_msgs:
                continue

            last_client = max(client_msgs, key=lambda m: m["created_at"])
            last_agent = max(agent_msgs, key=lambda m: m["created_at"]) if agent_msgs else None

            last_client_ts = datetime.fromisoformat(
                last_client["created_at"].replace("Z", "+00:00")
            )

            if last_agent:
                last_agent_ts = datetime.fromisoformat(
                    last_agent["created_at"].replace("Z", "+00:00")
                )
                if last_client_ts <= last_agent_ts:
                    continue  # Agent already responded

            minutes_waiting = int((now - last_client_ts).total_seconds() / 60)

            if minutes_waiting >= 5:
                stale.append({
                    "id": conv["id"],
                    "visitor_name": conv.get("visitor_name"),
                    "tunnel": conv.get("tunnel"),
                    "minutes_waiting": minutes_waiting,
                })

        return sorted(stale, key=lambda x: x["minutes_waiting"], reverse=True)

    except Exception as e:
        logger.error(f"get_pending_conversations error: {e}")
        return []


async def get_assigned_tasks(user_id: str) -> list[dict]:
    """Return active tasks assigned to *user_id* (not done/canceled)."""
    try:
        db_client = get_client()
        res = await _run_sync(
            lambda: (
                db_client.table("tasks")
                .select("id, task_number, title, priority, status, created_at")
                .eq("assignee_id", user_id)
                .not_.in_("status", '("done","canceled")')
                .order("created_at", desc=True)
                .limit(20)
                .execute()
            )
        )
        return res.data or []
    except Exception as e:
        logger.error(f"get_assigned_tasks error: {e}")
        return []
