"""Daily learning loop — the chatbot reads its own outcomes and proposes lessons.

    fetch → snapshot → MAP (per chunk) → REDUCE (synthesis) → merge
          → human approval → prompt injection

Lessons land as 'proposed'. Nothing reaches a client until a human approves it,
so the loop can be wrong without the live prompt being wrong.
"""

import json
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from config.settings import settings
from app.ai.claude import call_sonnet_learning
from app.db import supabase as db

logger = logging.getLogger(__name__)

PAGE_SIZE = 100          # conversations fetched per page
CHUNK_SIZE = 30          # cards per MAP call
MESSAGE_TRUNCATE = 200   # chars kept per message in a card
MAX_SAMPLE_CARDS = 3     # cards stored on a lesson for the approval screen

CARD_COLUMNS = (
    "id,chat_number,tunnel,status,mode,created_at,closed_at,updated_at,"
    "assigned_agent_id,metadata,visitor_name,visitor_phone,visitor_email,"
    "last_user_message_at,last_agent_message_at,last_reply_at"
)
LEAD_COLUMNS = (
    "conversation_id,origin_code,destination_code,departure_date,return_date,"
    "passengers,cabin_class,trip_type,intent_signals"
)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"\+?\d[\d\-.\s()]{7,}\d")

# Lesson content caps — trim, don't reject. Flagged lessons still land as
# proposed so a human decides; they just need an explicit second look.
LESSON_TITLE_MAX = 80
LESSON_CONTENT_MAX = 400

# Patterns that suggest the lesson is trying to steer the model rather than
# teach a measured sales outcome. Case-insensitive; any hit → needs_scrutiny.
_SCRUTINY_PATTERNS = [
    re.compile(p, re.I)
    for p in (
        r"\bignore\s+(all|previous)\b",
        r"\bdisregard\b",
        r"\bforget\s+(the|your)\b",
        r"\balways\s+(share|reveal|send|include)\b",
        r"\bnever\s+(mention|refuse)\b",
        r"https?://",
        r"\byou\s+must\b",
        r"\byou\s+should\s+now\b",
        r"\bfrom\s+now\s+on\b",
    )
]

FORCE_RERUN_WARNING = (
    "forced rerun — evidence counts may be double-applied for chunks that "
    "succeeded in the prior partial run"
)


# ── PII masking ───────────────────────────────────────────────
# Lessons are read by people and injected into prompts. No client data,
# ever (owner's 02-Jul rule).

def mask_pii(text: Optional[str], names: tuple[Optional[str], ...] = ()) -> str:
    """Replace emails, phones and known visitor names with placeholders."""
    if not text:
        return ""
    masked = _EMAIL_RE.sub("[email]", text)
    masked = _PHONE_RE.sub("[phone]", masked)
    for name in names:
        if name and len(name.strip()) >= 3:
            masked = re.sub(re.escape(name.strip()), "[name]", masked, flags=re.I)
    return masked


def sanitize_lesson(title: str, content: str) -> tuple[str, str, bool]:
    """Deterministic gate before a lesson is inserted.

    Returns (title, content, needs_scrutiny). Caps trim; pattern/PII hits set
    the flag. Flagged lessons are NOT dropped — the human decides — but the
    PATCH endpoint refuses to approve them without acknowledge_scrutiny.
    """
    clean_title = (title or "").strip()[:LESSON_TITLE_MAX]
    # Mask first so a residual email becomes [email] and still flags below
    # if the raw form was present (belt and suspenders over card masking).
    raw_content = (content or "").strip()
    has_pii = bool(_EMAIL_RE.search(raw_content) or _PHONE_RE.search(raw_content))
    clean_content = mask_pii(raw_content)[:LESSON_CONTENT_MAX]

    needs_scrutiny = has_pii or any(
        pat.search(clean_content) or pat.search(clean_title) for pat in _SCRUTINY_PATTERNS
    )
    return clean_title, clean_content, needs_scrutiny


# ── Conversation cards (no LLM — cheap) ───────────────────────

def _signals(lead: Optional[dict]) -> dict:
    signals = (lead or {}).get("intent_signals")
    return signals if isinstance(signals, dict) else {}


def _segment(conv: dict) -> str:
    """tunnel + traffic source — the axis the snapshot is sliced on."""
    meta = conv.get("metadata") or {}
    source = (meta.get("utm_source") or "direct").strip().lower()
    return f"{conv.get('tunnel') or 'sales'}/{source}"


def _duration_minutes(conv: dict) -> Optional[int]:
    start = db._parse_iso_dt(conv.get("created_at"))
    end = db._parse_iso_dt(conv.get("closed_at")) or db._parse_iso_dt(conv.get("updated_at"))
    if not start or not end or end < start:
        return None
    return int((end - start).total_seconds() // 60)


def _line(message: dict, names: tuple) -> dict:
    role = message.get("role") or "ai"
    text = mask_pii(message.get("content"), names)[:MESSAGE_TRUNCATE]
    return {"role": "customer" if role == "user" else role, "text": text}


def _excerpt_for_tag(tag: str, messages: list[dict], names: tuple) -> list[dict]:
    """The part of the transcript that explains this outcome."""
    if not messages:
        return []
    if tag == "abandoned":
        # The drop-off point: the last three exchanges before silence.
        picked = messages[-6:]
    elif tag == "no_engagement":
        # What failed to engage: our opening, since they never wrote.
        picked = messages[:3]
    elif tag == "completed":
        # The opening plus the exchange where contact landed.
        capture_at = next(
            (
                i
                for i, m in enumerate(messages)
                if m.get("role") == "user"
                and (_EMAIL_RE.search(m.get("content") or "") or _PHONE_RE.search(m.get("content") or ""))
            ),
            None,
        )
        picked = messages[:2]
        if capture_at is not None:
            picked = picked + messages[max(0, capture_at - 1): capture_at + 1]
    else:
        picked = messages[:2] + messages[-2:]
    return [_line(m, names) for m in picked]


def build_conversation_card(
    conv: dict, messages: list[dict], lead: Optional[dict] = None
) -> dict:
    """A masked, tag-aware summary of one conversation — the LLM's unit of input."""
    tag = db.derive_conversation_tag(conv, lead=lead)
    names = (conv.get("visitor_name"),)
    real = [m for m in messages if m.get("role") in ("user", "ai", "agent")]
    signals = _signals(lead)

    return {
        "chat": conv.get("chat_number") or conv.get("id"),
        "tag": tag,
        "tunnel": conv.get("tunnel") or "sales",
        "segment": _segment(conv),
        "message_count": len(real),
        "duration_min": _duration_minutes(conv),
        "occasion": signals.get("occasion"),
        "persona": signals.get("persona"),
        "excerpt": _excerpt_for_tag(tag, real, names),
    }


# ── Fetching ──────────────────────────────────────────────────

async def _fetch_conversation_page(bootstrap: bool, offset: int) -> list[dict]:
    client = db.get_client()

    def _query():
        q = client.table("conversations").select(CARD_COLUMNS)
        if bootstrap:
            q = q.eq("status", "closed")
        else:
            since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            q = q.gte("updated_at", since)
        return q.order("created_at", desc=True).range(offset, offset + PAGE_SIZE - 1).execute()

    res = await db._run_sync(_query)
    return res.data or []


async def _fetch_messages(conversation_ids: list[str]) -> dict[str, list[dict]]:
    """Messages for a page of conversations, oldest first, grouped by conversation."""
    if not conversation_ids:
        return {}
    client = db.get_client()

    def _query():
        return (
            client.table("messages")
            .select("conversation_id,role,content,created_at")
            .in_("conversation_id", conversation_ids)
            .order("created_at")
            .limit(PAGE_SIZE * 60)
            .execute()
        )

    res = await db._run_sync(_query)
    grouped: dict[str, list[dict]] = {}
    for row in res.data or []:
        grouped.setdefault(row["conversation_id"], []).append(row)
    return grouped


async def _fetch_leads(conversation_ids: list[str]) -> dict[str, dict]:
    if not conversation_ids:
        return {}
    client = db.get_client()

    def _query():
        return (
            client.table("leads")
            .select(LEAD_COLUMNS)
            .in_("conversation_id", conversation_ids)
            .execute()
        )

    res = await db._run_sync(_query)
    return {row["conversation_id"]: row for row in (res.data or [])}


async def collect_cards(bootstrap: bool) -> list[dict]:
    """All conversations in scope, as masked cards. Empty transcripts are skipped."""
    cards: list[dict] = []
    offset = 0
    while True:
        page = await _fetch_conversation_page(bootstrap, offset)
        if not page:
            break
        ids = [c["id"] for c in page]
        messages = await _fetch_messages(ids)
        leads = await _fetch_leads(ids)
        for conv in page:
            transcript = messages.get(conv["id"]) or []
            if not transcript:
                continue
            cards.append(build_conversation_card(conv, transcript, leads.get(conv["id"])))
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return cards


# ── Snapshot ──────────────────────────────────────────────────

def tag_distribution(cards: list[dict]) -> dict:
    """Daily raw snapshot: totals per tag, plus the same split by segment."""
    tags: dict[str, int] = {}
    segments: dict[str, dict[str, int]] = {}
    for card in cards:
        tag = card.get("tag") or "unknown"
        tags[tag] = tags.get(tag, 0) + 1
        bucket = segments.setdefault(card.get("segment") or "unknown", {})
        bucket[tag] = bucket.get(tag, 0) + 1
    return {"total": len(cards), "tags": tags, "segments": segments}


# ── LLM steps ─────────────────────────────────────────────────

_MAP_SYSTEM = """You analyze real sales-chat outcomes for a business class travel agency.

Each card is one conversation: its outcome tag, segment, and a masked excerpt.
Tags: completed (good lead captured) · abandoned (customer went quiet mid-way) ·
no_engagement (customer left contact but never wrote) · fresh/active/main_queue (in flight).

List the patterns that KILLED conversations and the patterns that CONVERTED them.
Every pattern must be about what the assistant SAID or FAILED TO SAY — phrasing,
ordering, timing — never vague advice like "be more empathetic".

Return ONLY valid JSON:
{"killer_patterns":[{"title":"short handle","content":"what to say / not say, with a masked example line","cards":["chat ids"],"segment":"tunnel/source"}],
 "winning_patterns":[{"title":"...","content":"...","cards":["..."],"segment":"..."}]}"""

_REDUCE_SYSTEM = """You consolidate patterns found across batches of real sales chats.

You get: the per-batch findings, and the lessons already on file (id, kind, title, status).

For each pattern worth keeping, emit one entry:
- "new" — not on file yet.
- "reinforces" — the same pattern as an existing lesson (give its lesson_id).
- "contradicts" — it contradicts an APPROVED lesson (give its lesson_id and say why
  in content). Contradictions are the most valuable output; never hide one.

Rules:
- Every content MUST carry phrasing-level guidance: what to say, what to avoid, and a
  masked example line. Reject and drop anything abstract ("be more empathetic").
- evidence_count = conversations supporting it. denominator = size of the relevant tag
  population in this run. dominant_segment = where it concentrates, e.g. "sales/kayak".
- Include at least 2 "few_shot" lessons when the material supports it: real masked
  exchanges that converted.
- At most 12 entries, best evidence first.

Return ONLY valid JSON:
{"lessons":[{"action":"new|reinforces|contradicts","lesson_id":"uuid or null",
"kind":"killer_pattern|winning_pattern|few_shot","title":"...","content":"...",
"evidence_count":1,"denominator":1,"dominant_segment":"...","cards":["chat ids"]}]}"""


def _parse_json(raw: Optional[str]) -> Optional[dict]:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _call_json(system: str, payload: str) -> tuple[Optional[dict], float]:
    """One LLM call that must return JSON. One retry, then give up on this chunk."""
    text, cost = call_sonnet_learning(system, payload)
    parsed = _parse_json(text)
    if parsed is not None:
        return parsed, cost

    retry_text, retry_cost = call_sonnet_learning(
        system, f"{payload}\n\nYour previous output was not valid JSON — return ONLY the JSON."
    )
    parsed = _parse_json(retry_text)
    if parsed is None:
        logger.warning("Learning: chunk skipped — model did not return valid JSON")
    return parsed, cost + retry_cost


# ── Lesson storage ────────────────────────────────────────────

async def get_lessons(status: Optional[str] = None) -> list[dict]:
    client = db.get_client()

    def _query():
        q = client.table("chatbot_lessons").select("*")
        if status:
            q = q.eq("status", status)
        return q.order("evidence_count", desc=True).limit(200).execute()

    res = await db._run_sync(_query)
    return res.data or []


async def get_lesson(lesson_id: str) -> Optional[dict]:
    client = db.get_client()

    def _query():
        return (
            client.table("chatbot_lessons")
            .select("*")
            .eq("id", lesson_id)
            .limit(1)
            .execute()
        )

    res = await db._run_sync(_query)
    return (res.data or [None])[0]


async def set_lesson_status(lesson_id: str, status: str) -> Optional[dict]:
    client = db.get_client()

    def _query():
        return (
            client.table("chatbot_lessons")
            .update({"status": status, "updated_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", lesson_id)
            .execute()
        )

    res = await db._run_sync(_query)
    return (res.data or [None])[0]


def _sample_cards(cards_by_id: dict, ids: Any) -> list[dict]:
    if not isinstance(ids, list):
        return []
    picked = [cards_by_id[str(cid)] for cid in ids if str(cid) in cards_by_id]
    return picked[:MAX_SAMPLE_CARDS]


async def merge_lessons(
    entries: list[dict], existing: list[dict], cards_by_id: dict, run_day: date
) -> tuple[int, int]:
    """Apply the REDUCE output. Returns (new_lessons, reinforced_lessons).

    A reinforced lesson keeps its status — approving something once must not be
    undone by tomorrow's run.
    """
    by_id = {str(lesson["id"]): lesson for lesson in existing}
    client = db.get_client()
    today = run_day.isoformat()
    new_count = 0
    reinforced = 0

    for entry in entries:
        action = (entry.get("action") or "new").lower()
        kind = entry.get("kind")
        if kind not in ("killer_pattern", "winning_pattern", "few_shot"):
            continue

        target = by_id.get(str(entry.get("lesson_id") or ""))
        if action == "reinforces" and target:
            payload = {
                "evidence_count": int(target.get("evidence_count") or 0)
                + max(1, int(entry.get("evidence_count") or 1)),
                "last_seen_run": today,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            if entry.get("denominator"):
                payload["denominator"] = int(entry["denominator"])
            if entry.get("dominant_segment"):
                payload["dominant_segment"] = entry["dominant_segment"]
            lesson_id = target["id"]

            def _update(p=payload, lid=lesson_id):
                return (
                    client.table("chatbot_lessons").update(p).eq("id", lid).execute()
                )

            await db._run_sync(_update)
            reinforced += 1
            continue

        title = (entry.get("title") or "").strip()
        content = (entry.get("content") or "").strip()
        if not title or not content:
            continue

        title, content, needs_scrutiny = sanitize_lesson(title, content)
        if not title or not content:
            continue

        row = {
            "kind": kind,
            "title": title,
            "content": content,
            "evidence_count": max(1, int(entry.get("evidence_count") or 1)),
            "denominator": int(entry["denominator"]) if entry.get("denominator") else None,
            "dominant_segment": entry.get("dominant_segment"),
            "sample_cards": _sample_cards(cards_by_id, entry.get("cards")),
            "status": "proposed",
            "needs_scrutiny": needs_scrutiny,
            "first_seen_run": today,
            "last_seen_run": today,
        }
        if action == "contradicts" and target:
            row["contradicts_lesson_id"] = target["id"]

        def _insert(r=row):
            return client.table("chatbot_lessons").insert(r).execute()

        await db._run_sync(_insert)
        new_count += 1

    return new_count, reinforced


# ── Runs table ────────────────────────────────────────────────

async def _prior_run_today(run_day: date) -> Optional[dict]:
    """Any run for today, any status. A partial must block re-run too —
    otherwise evidence on the chunks that succeeded gets double-counted."""
    client = db.get_client()

    def _query():
        return (
            client.table("learning_runs")
            .select("id,status")
            .eq("run_date", run_day.isoformat())
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )

    res = await db._run_sync(_query)
    return (res.data or [None])[0]


async def _run_exists_today(run_day: date) -> bool:
    """Back-compat wrapper — True when any prior run for today exists."""
    return await _prior_run_today(run_day) is not None


async def _approved_lesson_ids() -> list[str]:
    """Sorted ids of currently approved lessons — the day's provenance snapshot."""
    approved = await get_lessons(status="approved")
    return sorted(str(lesson["id"]) for lesson in approved if lesson.get("id"))


async def _save_run(payload: dict) -> None:
    client = db.get_client()

    def _insert():
        return client.table("learning_runs").insert(payload).execute()

    await db._run_sync(_insert)


async def _recent_runs(days: int = 14) -> list[dict]:
    since = (date.today() - timedelta(days=days)).isoformat()
    client = db.get_client()

    def _query():
        return (
            client.table("learning_runs")
            .select("run_date,tag_distribution")
            .gte("run_date", since)
            .order("run_date", desc=True)
            .limit(days * 2)
            .execute()
        )

    res = await db._run_sync(_query)
    return res.data or []


async def _prune_runs() -> None:
    cutoff = (date.today() - timedelta(days=settings.learning_runs_retention_days)).isoformat()
    client = db.get_client()

    def _delete():
        return client.table("learning_runs").delete().lt("run_date", cutoff).execute()

    try:
        await db._run_sync(_delete)
    except Exception as e:  # retention must never fail a run
        logger.warning(f"Learning: run retention prune failed: {e}")


# ── Rolling window + regression watchdog ──────────────────────

def _abandoned_share(runs: list[dict]) -> Optional[float]:
    total = 0
    abandoned = 0
    for run in runs:
        tags = ((run.get("tag_distribution") or {}).get("tags")) or {}
        total += sum(tags.values())
        abandoned += tags.get("abandoned", 0)
    if not total:
        return None
    return abandoned / total


def rolling_summary(runs: list[dict], today: date) -> dict:
    """Rolling 7-day view plus the prior 7 days — the alert math and the report."""
    current, prior = [], []
    for run in runs:
        try:
            run_day = date.fromisoformat(str(run.get("run_date")))
        except (TypeError, ValueError):
            continue
        age = (today - run_day).days
        if 0 <= age < 7:
            current.append(run)
        elif 7 <= age < 14:
            prior.append(run)
    return {
        "abandoned_share_7d": _abandoned_share(current),
        "abandoned_share_prior_7d": _abandoned_share(prior),
    }


def regression_detected(summary: dict) -> bool:
    current = summary.get("abandoned_share_7d")
    prior = summary.get("abandoned_share_prior_7d")
    if not current or not prior:
        return False
    return ((current - prior) / prior) * 100.0 > settings.learning_regression_alert_pct


async def _send_regression_alert(summary: dict) -> bool:
    """Tell the owner the abandoned share jumped. Rates only — no client data."""
    if not settings.postmark_token:
        logger.warning("POSTMARK_TOKEN not set — skipping regression alert")
        return False

    current = (summary.get("abandoned_share_7d") or 0) * 100
    prior = (summary.get("abandoned_share_prior_7d") or 0) * 100
    link = f"{settings.admin_panel_url.rstrip('/')}/chats"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(
                "https://api.postmarkapp.com/email",
                headers={
                    "X-Postmark-Server-Token": settings.postmark_token,
                    "Content-Type": "application/json",
                },
                json={
                    "From": settings.email_from,
                    "To": settings.super_alert_email,
                    "Subject": "Chatbot regression — abandoned share is up",
                    "MessageStream": "outbound",
                    "HtmlBody": (
                        "<h2>Abandoned share rose sharply</h2>"
                        f"<p>Last 7 days: <strong>{current:.1f}%</strong><br>"
                        f"Previous 7 days: {prior:.1f}%</p>"
                        f'<p><a href="{link}">Open the admin panel</a></p>'
                    ),
                    "TextBody": (
                        "Abandoned share rose sharply.\n\n"
                        f"Last 7 days: {current:.1f}%\n"
                        f"Previous 7 days: {prior:.1f}%\n\n"
                        f"Open: {link}\n"
                    ),
                },
            )
            return res.status_code == 200
    except Exception as e:
        logger.error(f"Regression alert failed: {e}")
        return False


# ── The run ───────────────────────────────────────────────────

async def run_learning(bootstrap: bool = False, force: bool = False) -> dict:
    """One pass of the loop. Never raises — a failed run is recorded, not thrown.

    `force=True` bypasses the already-ran guard. Evidence-unsafe: chunks that
    succeeded in a prior partial will be reinforced again. Documented, rare.
    """
    run_day = date.today()
    budget = settings.learning_bootstrap_budget if bootstrap else settings.learning_run_budget

    prior = await _prior_run_today(run_day)
    if prior and not force:
        logger.info(
            f"Learning: already ran today (status={prior.get('status')}) — skipping"
        )
        return {
            "skipped": "already_ran",
            "prior_status": prior.get("status"),
            "run_date": run_day.isoformat(),
        }

    status = "ok"
    cost = 0.0
    new_lessons = reinforced = 0
    cards: list[dict] = []
    distribution: dict = {}
    active_lesson_ids = await _approved_lesson_ids()

    try:
        cards = await collect_cards(bootstrap)
        distribution = tag_distribution(cards)
        logger.info(
            f"Learning: {len(cards)} conversations | bootstrap={bootstrap} | "
            f"force={force} | tags={distribution.get('tags')}"
        )

        chunk_findings: list[dict] = []
        skipped_chunks = 0
        if cards:
            for start in range(0, len(cards), CHUNK_SIZE):
                if cost >= budget:
                    status = "partial_budget"
                    logger.warning(f"Learning: budget cap ${budget} hit — stopping MAP early")
                    break
                chunk = cards[start:start + CHUNK_SIZE]
                parsed, chunk_cost = _call_json(_MAP_SYSTEM, json.dumps(chunk, default=str))
                cost += chunk_cost
                if parsed is None:
                    skipped_chunks += 1
                    continue
                chunk_findings.append(parsed)

        existing = await get_lessons()
        if chunk_findings and cost < budget:
            reduce_payload = json.dumps(
                {
                    "batches": chunk_findings,
                    "existing_lessons": [
                        {
                            "id": lesson["id"],
                            "kind": lesson.get("kind"),
                            "title": lesson.get("title"),
                            "status": lesson.get("status"),
                        }
                        for lesson in existing
                    ],
                    "tag_distribution": distribution,
                },
                default=str,
            )
            reduced, reduce_cost = _call_json(_REDUCE_SYSTEM, reduce_payload)
            cost += reduce_cost
            if reduced is None:
                skipped_chunks += 1
            else:
                cards_by_id = {str(card["chat"]): card for card in cards}
                new_lessons, reinforced = await merge_lessons(
                    reduced.get("lessons") or [], existing, cards_by_id, run_day
                )

        if skipped_chunks and status == "ok":
            status = "partial_json"

    except Exception as e:
        logger.error(f"Learning run failed: {type(e).__name__}: {e}", exc_info=True)
        await _save_run(
            {
                "run_date": run_day.isoformat(),
                "bootstrap": bootstrap,
                "conversations_analyzed": len(cards),
                "tag_distribution": distribution or None,
                "active_lesson_ids": active_lesson_ids,
                "cost": round(cost, 4),
                "status": "error",
                "error": f"{type(e).__name__}: {e}"[:500],
            }
        )
        return {"status": "error", "error": str(e), "cost": round(cost, 4)}

    await _save_run(
        {
            "run_date": run_day.isoformat(),
            "bootstrap": bootstrap,
            "conversations_analyzed": len(cards),
            "tag_distribution": distribution,
            "active_lesson_ids": active_lesson_ids,
            "new_lessons": new_lessons,
            "reinforced_lessons": reinforced,
            "cost": round(cost, 4),
            "status": status,
        }
    )

    summary = rolling_summary(await _recent_runs(), run_day)
    alerted = False
    if not bootstrap and regression_detected(summary):
        alerted = await _send_regression_alert(summary)

    await _prune_runs()
    invalidate_lesson_cache()

    result = {
        "status": status,
        "bootstrap": bootstrap,
        "run_date": run_day.isoformat(),
        "conversations_analyzed": len(cards),
        "tag_distribution": distribution,
        "active_lesson_ids": active_lesson_ids,
        "new_lessons": new_lessons,
        "reinforced_lessons": reinforced,
        "cost": round(cost, 4),
        "rolling_7d": summary,
        "regression_alert_sent": alerted,
    }
    if force and prior:
        result["warning"] = FORCE_RERUN_WARNING
    return result


# ── Prompt injection (sync — the generator runs in a worker thread) ──

_LESSON_CACHE: dict[str, Any] = {"at": 0.0, "lessons": []}
_LESSON_CACHE_TTL = 600  # seconds


def invalidate_lesson_cache() -> None:
    _LESSON_CACHE["at"] = 0.0
    _LESSON_CACHE["lessons"] = []


def _fetch_approved_lessons() -> list[dict]:
    """Approved, still-fresh lessons, best evidence first."""
    stale_before = (date.today() - timedelta(days=settings.lesson_staleness_days)).isoformat()
    client = db.get_client()
    res = (
        client.table("chatbot_lessons")
        .select("kind,title,content,evidence_count,denominator")
        .eq("status", "approved")
        .gte("last_seen_run", stale_before)
        .order("evidence_count", desc=True)
        .limit(settings.lesson_injection_limit)
        .execute()
    )
    return res.data or []


def approved_lessons(max_age_seconds: int = _LESSON_CACHE_TTL) -> list[dict]:
    """Cached lesson list — the prompt asks for this on every turn.

    Empty results and failures are cached too: with no approved lessons (or the
    migration not applied yet) we must not pay a round-trip per reply.
    """
    now = time.time()
    if now - _LESSON_CACHE["at"] < max_age_seconds:
        return _LESSON_CACHE["lessons"]
    try:
        lessons = _fetch_approved_lessons()
    except Exception as e:
        logger.warning(f"Lesson fetch failed (prompt continues without them): {e}")
        lessons = _LESSON_CACHE["lessons"] or []
    _LESSON_CACHE["at"] = now
    _LESSON_CACHE["lessons"] = lessons
    return lessons


MAX_LESSON_SECTION_CHARS = 2400  # ~600 tokens


def render_lessons_section(lessons: list[dict]) -> Optional[str]:
    """The prompt block. None when nothing is approved — no empty header."""
    if not lessons:
        return None

    lines = [
        "LEARNED FROM REAL CONVERSATIONS (follow these — they come from measured outcomes):"
    ]
    for lesson in lessons[: settings.lesson_injection_limit]:
        evidence = lesson.get("evidence_count") or 0
        denominator = lesson.get("denominator")
        proof = f"{evidence}/{denominator} chats" if denominator else f"{evidence} chats"
        line = f"- [{lesson.get('kind')}] {lesson.get('content')} (evidence: {proof})"
        candidate = "\n".join(lines + [line])
        if len(candidate) > MAX_LESSON_SECTION_CHARS:
            break
        lines.append(line)

    return "\n".join(lines) if len(lines) > 1 else None
