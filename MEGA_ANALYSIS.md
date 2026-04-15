# BBC AI Chatbot — MEGA ANALYSIS

> **Generated from complete read of 30+ source files.**
> Every claim backed by exact file + line quotes.

---

## A. CLAUDE API (`app/ai/claude.py` — 130 lines)

### A1. Client initialization
```python
_client: anthropic.Anthropic | None = None

def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _client
```
**Singleton pattern.** One shared `Anthropic` instance, created lazily on first call. Uses sync SDK (not `AsyncAnthropic`).

### A2. Models used
| Function | Model | max_tokens | temperature | Purpose |
|----------|-------|-----------|-------------|---------|
| `call_haiku()` | `claude-3-5-haiku-20241022` | 300 | 0.3 | Standard responses |
| `call_sonnet()` | `claude-sonnet-4-20250514` | 500 | 0.4 | Complex conversations |
| `classify_intent()` | (Haiku) | 20 | 0 | Intent classification |

Model strings from `config/settings.py`:
```python
claude_haiku_model: str = "claude-3-5-haiku-20241022"
claude_sonnet_model: str = "claude-sonnet-4-20250514"
```

### A3. Timeout
```python
timeout=settings.claude_timeout  # default: 8 seconds
```

### A4. Cost estimation
```python
COSTS: dict[str, dict[str, float]] = {
    "claude-3-5-haiku-20241022": {
        "input": 0.25 / 1_000_000,   # $0.25/M input
        "output": 1.25 / 1_000_000,  # $1.25/M output
    },
    "claude-sonnet-4-20250514": {
        "input": 3.0 / 1_000_000,    # $3.0/M input
        "output": 15.0 / 1_000_000,  # $15.0/M output
    },
}
```

### A5. Error handling — NO RETRY
```python
def _call_model(...) -> tuple[Optional[str], float]:
    try:
        response = _get_client().messages.create(...)
        return text, cost
    except anthropic.APITimeoutError:
        return None, 0.0
    except anthropic.APIError as e:
        return None, 0.0
    except Exception as e:
        return None, 0.0
```
**Single attempt.** On ANY failure: returns `(None, 0.0)`. No retry, no backoff. `tenacity` is in `requirements.txt` but **never imported or used anywhere**.

### A6. What happens if ANTHROPIC_API_KEY is empty?
`settings.anthropic_api_key` defaults to `""`. The client is created with `api_key=""`. API calls fail with `APIError` (authentication), caught by except, returns `(None, 0.0)`. The pipeline then falls through to template fallback.

---

## B. PIPELINE FLOW (`app/pipeline/orchestrator.py` — ~300 lines)

### B1. The 8 Steps

```
Step 1: GET/CREATE CONVERSATION → supabase.get_or_create_conversation()
Step 2: AGENT CHECK (V3 placeholder — always AI mode)
Step 3: INTENT DETECTION → intent.detect_intent()
Step 3.6: MULTI-TURN PROBE DETECTION (11 keywords, threshold ≥3)
Step 3.5: AGENT HANDOFF CHECK (8 keywords, threshold ≥2)
Step 4: ENTITY EXTRACTION → entity_extractor.extract_entities()
Step 5: KB SEARCH → Qdrant (if enabled) → keyword fallback
Step 6: GENERATE RESPONSE → generator.generate_response()
Step 7: VALIDATE OUTPUT → validator.validate_response()
Step 8: DELIVER + refusal detection + auto-summarize + pipeline_run recording
```

### B2. Timeout wrapper
```python
async def process_message(...) -> ChatResponse:
    try:
        return await asyncio.wait_for(
            _pipeline(...),
            timeout=settings.pipeline_timeout,  # default: 10 seconds
        )
    except asyncio.TimeoutError:
        return ChatResponse(
            conversation_id=conversation_id or "unknown",
            message=get_template("ai_fallback", tunnel, visitor),
            type="template_fallback", model_used="template",
        )
```

### B3. History is fetched EARLY (Bug #1 fix)
```python
# Save user message immediately (never lose data)
user_msg = await conversation_service.add_message(...)

# Fetch history early — needed by Steps 3.5, 3.6, and 6
history = await db.get_recent_messages(cid, limit=10)
```
History is fetched **after** saving the user message and **before** Steps 3.5/3.6. It's re-fetched at Step 6 for generation context.

### B4. Step 3.6 — Multi-turn probe detection
```python
_probe_keywords = [
    "guidelines", "instructions", "system prompt", "your rules",
    "who made you", "what model", "what version", "your prompt",
    "anthropic", "how were you trained", "your programming",
]
# Warn at ≥3 probe messages in history
```
**Detection only — does NOT block.** Logs `SECURITY: Multi-turn probe detected` but doesn't change the response.

### B5. Step 3.5 — Agent handoff
```python
agent_keywords = ["agent", "human", "person", "someone", "speak",
                  "talk to", "representative", "real person"]
if agent_request_count >= 2:
    await db.update_conversation(cid, {"status": "needs_agent"})
```
Sets conversation status to `"needs_agent"` after 3+ requests (current + 2 previous).

### B6. Step 5 — KB search cascade
```
1. If qdrant_enabled and qdrant_url → Qdrant vector search (server-side embedding)
2. If Qdrant returns nothing or fails → keyword_search_kb via Supabase full-text
3. Skip KB entirely for GREETING, CLOSING, TALK_TO_AGENT intents
```

### B7. Step 8 — Refusal detection
```python
_refusal_signals = [
    "i can't assist", "i cannot assist", "i'm not able to",
    "i can't help with", "i cannot help with",
    "i must decline", "i'm unable to",
]
if _is_refusal and len(validated_text) < 100:
    validated_text = get_template("ai_fallback", tunnel, visitor)
```
Only replaces if refusal message is short (<100 chars). Long refusals pass through.

### B8. Auto-summarize
```python
total_msgs = len(history) + 2
if total_msgs >= 5 and total_msgs % 5 == 0:
    # Call Haiku with summary prompt, save to conversation.summary
```
Every 5th message, generates a 2-sentence summary via Haiku.

---

## C. INTENT DETECTION (`app/pipeline/intent.py` — 130 lines)

### C1. 18 Intent enum values
```
NEW_BOOKING, PRICE_INQUIRY, ROUTE_INFO, BOOKING_CHANGE, BAGGAGE_INFO,
GENERAL_QUESTION, GREETING, CLOSING, TALK_TO_AGENT,
SEAT_SELECTION, MEAL_PREFERENCE, LOUNGE_ACCESS, CHECK_IN,
VISA_INFO, TRAVEL_INSURANCE, PAYMENT_METHODS, RECEIPT_REQUEST, OTHER
```

### C2. 16 regex patterns (ordered most-specific → generic)
```
TALK_TO_AGENT → GREETING → CLOSING → BOOKING_CHANGE → BAGGAGE_INFO →
PRICE_INQUIRY → SEAT_SELECTION → MEAL_PREFERENCE → LOUNGE_ACCESS →
CHECK_IN → VISA_INFO → TRAVEL_INSURANCE → PAYMENT_METHODS →
RECEIPT_REQUEST → NEW_BOOKING → ROUTE_INFO
```
**GENERAL_QUESTION and OTHER have NO regex** — only reachable via Claude classifier or default.

### C3. 4-step detection
```
1. metadata.quick_reply_intent → instant, free
2. Regex patterns → <5ms, free
3. Claude Haiku classifier → <500ms, paid (max_tokens=20, temp=0)
4. Default → GENERAL_QUESTION
```

---

## D. RESPONSE GENERATION (`app/pipeline/generator.py` — ~295 lines)

### D1. Decision tree (full cascade)
```
1. GREETING → template ($0)
2. CLOSING → template ($0)
3. TALK_TO_AGENT → template ($0)
4. NEW_BOOKING/ROUTE_INFO + route KB data → route_card + template ($0)
4.5a. Intent-specific templates:
      BAGGAGE_INFO, PRICE_INQUIRY, BOOKING_CHANGE(support only)
      + 8 Support V2: SEAT_SELECTION, MEAL_PREFERENCE, LOUNGE_ACCESS,
        CHECK_IN, VISA_INFO, TRAVEL_INSURANCE, PAYMENT_METHODS, RECEIPT_REQUEST
4.5b. Smart routing (SALES only): lead has data → ask missing fields
      Order: route → dates → phone → email → name → specialist_handoff
      Loop prevention: checks if last AI message already asked
4.9. Budget guard: budget_remaining ≤ 0 → ai_fallback ($0)
5a. Sonnet: ≥5 user messages OR BOOKING_CHANGE intent
5b. Haiku: all other cases
5c. Fallback template if AI fails
```

### D2. Sonnet trigger conditions
```python
use_sonnet = len(user_messages) >= 5 or intent == Intent.BOOKING_CHANGE
```
NOT a dollar cap. It's message count + intent.

### D3. Wallet protection
```python
if use_sonnet and len(entities.get("_raw_message", "")) > 1500:
    logger.info("Wallet protection: long message → forcing Haiku")
    use_sonnet = False
```
Messages >1500 chars force Haiku even when Sonnet would be triggered.

### D4. Route card extraction
```python
def _build_route_card_from_kb(kb_result: KBResult) -> RouteCard | None:
    # Heuristic: title must contain " to " separator
    # Extracts: price from $X–$Y regex, duration, airlines, airport codes
```

---

## E. TEMPLATES (`app/ai/templates.py` — ~260 lines)

### E1. Total template keys: **42**

| Category | Keys |
|----------|------|
| Sales core | welcome, welcome:anonymous, route_card_response, ask_name, ask_email, ask_phone, ask_dates, ask_passengers, confirm_route, specialist_handoff, lead_captured, closing (12) |
| Support core | welcome, welcome:anonymous, closing (3) |
| Intent-specific | baggage_info, price_inquiry, booking_change (3) |
| Universal | talk_to_agent, handoff_confirmed, after_hours, ai_fallback, rate_limited, error (6) |
| Sales V2 | route_info_generic, general_question, other, new_booking_no_route, returning_visitor, first_class_inquiry, corporate_inquiry (7) |
| Support V2 | route_info_generic, general_question, other, seat_selection, meal_preference, lounge_access, check_in, visa_info, travel_insurance, payment_methods, receipt_request (11) |

### E2. Lookup order
```python
candidates = []
if not has_name:
    candidates.append(f"{key}:{tunnel}:anonymous")  # 1st priority
candidates.append(f"{key}:{tunnel}")                 # 2nd priority
candidates.append(key)                               # 3rd (bare key)
```

### E3. Placeholder replacement
```python
replacements = {
    "name": visitor.name or "",
    "name_suffix": f", {visitor.name}" if visitor.name else "",
    "contact": visitor.phone or visitor.email or "the number you provided",
    **kwargs,
}
```

---

## F. VALIDATION (`app/pipeline/validator.py` — ~96 lines)

### F1. 5 checks + XSS strip
```
1. Exact prices without qualifiers → "contact our specialists for current pricing"
2. 9 competitor names → "other services"
   kayak, expedia, google flights, skyscanner, momondo, cheapoair, priceline, hopper, kiwi
3. 6 hallucination phrases → specialist redirect
4. Length >500 chars → truncate at sentence boundary
5. Empty → "How can I help you with business class travel today?"
6. XSS: re.sub(r"<[^>]+>", "", result)  # strip all HTML tags from output
```

### F2. Price regex (negative lookbehind)
```python
_EXACT_PRICE_RE = re.compile(
    r"(?<!\btypically\s)(?<!\baround\s)(?<!\brange\s)"
    r"\$\d{1,3}(?:,\d{3})*(?:\.\d{2})?\b"
)
```
Allows prices preceded by "typically", "around", or "range".

---

## G. ENTITY EXTRACTION (`app/pipeline/entity_extractor.py` — 290 lines)

### G1. ExtractedEntities dataclass
```
email, phone, name, origin_code, destination_code,
passengers, cabin_class, departure_date, return_date
```

### G2. Extraction coverage
| Entity | Method | Size |
|--------|--------|------|
| Email | Regex `EMAIL_RE` | Standard email pattern |
| Phone | Regex `PHONE_RE` | International format |
| Name | 5 regex patterns (my name is, i'm, call me, this is, i am) | — |
| Airport | 50 IATA codes set | `{"JFK","LAX","LHR","CDG",...}` |
| City → Code | 32 mappings dict | `{"new york":"JFK","london":"LHR",...}` |
| Passengers | Regex + "just me"/"two of us" handlers | — |
| Cabin class | Regex for business/first/economy/premium | — |
| Dates | 3 strategies: same-month range, named dates, numeric dates | — |
| KB keywords | Extract keywords minus stopwords, boost airport codes, max 5 | — |

---

## H. SECURITY

### H1. Input sanitizer (`app/security/input_sanitizer.py`)

**29 INJECTION_PATTERNS** in 8 categories:
```
Original 9: ignore previous, you are now, system prompt, reveal your,
            act as, pretend, forget everything, new instructions, XML tags
Roleplay/DAN (6): from now on you're, roleplay as, let's pretend,
                   DAN no restrictions, in character, you are now [NAME]  
Ethical Dilemma (3): hypothetical, emergency/life depend, in a world where
Encoding (2): decode/translate and follow, base64 strings
System Prompt Extraction (4): repeat everything above, first instruction,
                               start your response with, print your prompt
Privilege Escalation (3): developer/admin/sudo mode, I work at anthropic,
                          enable unrestricted
Multi-Language (1): FR/ES/RU ignore variants
```

**5-step sanitize_message():**
```
1. strip()
2. Truncate to MAX_LENGTH (2000 chars)
3. Remove HTML tags
4. Injection check → if suspicious: BLOCKS with SAFE_FALLBACK
5. Collapse excessive newlines (3+ → 2)
```

Severity logging:
- 1 pattern match → `WARNING`
- 3+ pattern matches → `CRITICAL` ("coordinated injection attempt")

### H2. KB content sanitization (`app/ai/prompts.py`)

**8 _KB_POISON_PATTERNS** — prevents indirect injection via poisoned KB entries:
```python
_KB_POISON_PATTERNS = [
    r"ignore\s+(all\s+)?previous",
    r"(new|override|change)\s+instructions?",
    r"you\s+(are|must|should)\s+now",
    r"(system|original)\s+prompt",
    r"respond\s+(only|always)\s+with",
    r"<\|?(system|user|assistant)\|?>",
    r"from\s+now\s+on",
    r"\[INST\]|\[/INST\]|<<SYS>>|<</SYS>>",
]
```
Applied to:
- KB results content before injection into prompt
- **Conversation history messages** — sanitized via `_sanitize_kb_content()` on each message's content

### H3. Auth (`app/security/auth.py`)

```python
# Dev mode: no auth configured → allow everything
if not settings.api_user or not settings.api_pass:
    return {"id": "dev", "role": "owner", "name": "dev"}
```

**Basic Auth**: Checked with `secrets.compare_digest` (timing-safe).

**Bearer Token V1 — SECURITY CONCERN:**
```python
if auth.startswith("Bearer "):
    token = auth[7:].strip()
    if not token:
        raise HTTPException(401, "Empty Bearer token")
    # V1: accept any non-empty token, return default admin user.
    return {"id": "admin", "role": "owner", "name": "admin"}
```
**ANY non-empty Bearer token grants full admin access.** Documented as V1 placeholder; V2 should validate JWT.

### H4. Rate limiter (`app/security/rate_limiter.py`)

**In-memory per-IP rate limiter** (NOT distributed — resets on restart/redeploy):
```
burst: 15 tokens (token bucket, refill rate = 1/rate_sustained_seconds)
hourly: 100 messages
daily: 300 messages
```
IP extraction: `X-Forwarded-For` header first, falls back to `request.client.host`.

### H5. Pipeline security summary

| Layer | Protection | Location |
|-------|-----------|----------|
| Input | 29 injection patterns + HTML strip + 2000 char limit | `input_sanitizer.py` |
| KB | 8 poison patterns on KB content + history | `prompts.py._sanitize_kb_content()` |
| Output | Price strip + competitor strip + hallucination detect + XSS strip | `validator.py` |
| Rate | Token bucket + hourly + daily per-IP | `rate_limiter.py` |
| Auth | Basic + Bearer (V1: any token) | `auth.py` |
| Probe | Multi-turn probe detection (log only, no block) | `orchestrator.py` Step 3.6 |
| Handoff | Auto-escalate after 3+ agent requests | `orchestrator.py` Step 3.5 |
| Refusal | Detect Claude refusals, replace with fallback | `orchestrator.py` Step 8 |
| Budget | Daily budget guard + wallet protection (>1500 chars → Haiku) | `generator.py` |
| Tools | Zero Trust executor with param validation, budget control, audit | `executor.py` |

---

## I. DATABASE (`app/db/supabase.py` — ~690 lines)

### I1. Connection
```python
_client: Optional[Client] = None
_executor = ThreadPoolExecutor(max_workers=20)

async def _run_sync(fn):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, fn)
```
**supabase-py is SYNC.** Every DB call is wrapped in `_run_sync()` to avoid blocking the async event loop. Thread pool of 20 workers.

### I2. Tables accessed
| Table | Operations |
|-------|-----------|
| `conversations` | SELECT, INSERT, UPDATE |
| `messages` | SELECT, INSERT (count with `count="exact"`) |
| `kb_entries` | SELECT, INSERT, UPDATE, DELETE |
| `kb_categories` | SELECT (with nested kb_entries count) |
| `leads` | SELECT (with JOIN on conversations), UPDATE |
| `route_segments` | SELECT (by lead_id) |
| `pipeline_runs` | INSERT, SELECT (for cost/stats) |
| `users` | SELECT, UPDATE |

### I3. Dashboard statistics (`get_dashboard_stats()`)
Single massive function (~200 lines). Fetches ALL conversations, ALL leads, ALL pipeline_runs, then processes in Python. Returns:
```
conversations_{today,yesterday,week,month,active}
leads_{total,new,contacted,qualified,converted,lost,gold,silver,bronze,uncalled,sla_breach}
cost_{today,week,month,avg_30d,vs_budget_percent}
latency_median_ms, fallback_rate_percent, avg_duration_minutes
messages_total_month
top_routes (top 5), conversations_trend (14d), leads_trend (14d)
conversations_trend_v2 (sales/support split), hot_leads (top 5 by score)
leads_sparkline_7d, funnel (5-stage)
```

**Performance note:** Fetches ALL rows from conversations, leads, pipeline_runs, then filters in Python. No server-side aggregation. Works for small datasets but will scale poorly.

### I4. Lead scoring
From `app/services/lead_service.py`:
```python
def _calculate_score(lead: dict, conv: Optional[dict] = None) -> int:
    score = 0
    if has_name:   score += 20
    if has_phone:  score += 15
    if has_email:  score += 10
    if has_route:  score += 15
    if has_dates:  score += 10
    if has_pax:    score += 10
    return min(score, 100)
```
Max possible: 80 (20+15+10+15+10+10). Gold threshold is 80 — requires ALL fields.

### I5. Tier thresholds
```python
gold: score >= 80
silver: score >= 50
bronze: score < 50
```

---

## J. QDRANT (`app/db/qdrant.py` — ~165 lines)

### J1. Architecture
```
Client: httpx REST API (NOT qdrant-client SDK)
Model: sentence-transformers/all-MiniLM-L6-v2 (384d)
Embedding: SERVER-SIDE by Qdrant Cloud — zero client-side embedding
Vector size: 384, Distance: Cosine
Timeout: 5.0 seconds
```

### J2. Collection management
```python
def _ensure_collection_sync() -> bool:
    # DESTRUCTIVE: deletes existing collection, creates new one
    client.delete(_collection_url())
    create_body = {
        "vectors": {"size": 384, "distance": "Cosine", "on_disk": True},
    }
    client.put(_collection_url(), json=create_body)
```

### J3. Upsert (embedding happens server-side)
```python
body = {
    "points": [{
        "id": entry_id,
        "vector": {"text": text, "model": _EMBEDDING_MODEL},  # Qdrant embeds this
        "payload": {"title": title, "content": content, "tunnel": tunnel, "category_id": category_id},
    }]
}
```

### J4. Search with tunnel filter
```python
body = {
    "query": {"text": query_text, "model": _EMBEDDING_MODEL},
    "limit": 3,
    "filter": {"must": [{"key": "tunnel", "match": {"value": tunnel}}]},
}
```

### J5. Graceful degradation
If `qdrant_url` or `qdrant_api_key` is empty, `_get_client()` returns `None`, and all operations silently return empty/False.

---

## K. FRONTEND INTEGRATION

### K1. Chat endpoint (`app/api/chat.py`)
```python
@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest, _rate: None = Depends(check_rate_limit)):
    clean_message = sanitize_message(req.message)
    if is_suspicious(req.message):
        logger.warning(f"Suspicious message detected (conv={req.conversation_id})")
    if req.conversation_id:
        count = await db.count_messages(req.conversation_id)
        if count >= settings.max_messages_per_conversation:  # default: 50
            raise HTTPException(429, "Conversation message limit reached")
    response = await process_message(...)
    return response
```
**PUBLIC endpoint** — no auth required (for customer widget).

### K2. Admin endpoints (ALL auth-protected)
```python
admin_deps = [Depends(get_current_user)]
app.include_router(conversations_router, prefix="/api", ..., dependencies=admin_deps)
app.include_router(leads_router,         prefix="/api", ..., dependencies=admin_deps)
app.include_router(kb_router,            prefix="/api", ..., dependencies=admin_deps)
app.include_router(dashboard_router,     prefix="/api", ..., dependencies=admin_deps)
app.include_router(users_router,         prefix="/api", ..., dependencies=admin_deps)
```

### K3. CORS whitelist
```python
cors_origins: list[str] = [
    "https://buybusinessclass.com",
    "https://www.buybusinessclass.com",
    "http://localhost:5173",
    "http://localhost:5174",
    "https://bbc-admin.vercel.app",
    "https://admin-panel-error.vercel.app",
]
```

### K4. Health check (PUBLIC)
```python
@router.get("/health")
async def health():
    db_ok = await check_connection()
    claude_ok = bool(settings.anthropic_api_key)
    # Returns: status (healthy/degraded/critical), version, services
```

---

## L. MODELS

### L1. Chat models (`app/models/chat.py`)
```python
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    conversation_id: Optional[str] = None
    tunnel: str = Field(default="sales", pattern="^(sales|support)$")
    visitor: VisitorInfo = Field(default_factory=VisitorInfo)
    metadata: Optional[dict] = None

class ChatResponse(BaseModel):
    conversation_id: str
    message: str
    type: str              # "template" | "ai" | "template_fallback"
    model_used: str
    cost: float = 0.0
    route_card: Optional["RouteCard"] = None  # Bug #2 fix: was Optional[dict]

class RouteCard(BaseModel):
    origin: str
    destination: str
    airlines: str = ""
    duration: str = ""
    price_range: str = ""
```

### L2. Admin models (`app/models/admin.py` — 210 lines)
```
ConversationListItem, MessageItem, ConversationDetail, ConversationUpdate
LeadListItem(+JOINed visitor_*), LeadFull(+route_segments), LeadStatusUpdate
RouteSegmentItem (id, lead_id, segment_order, origin/dest codes+cities, dates, cabin, transport)
UserUpdate (role, is_active)
KBCategoryItem, KBEntryItem, KBEntryCreate, KBEntryUpdate
DashboardStats (30+ fields including V2: hot_leads, funnel, sparkline, trend_v2)
```

### L3. Lead model (`app/models/lead.py`)
```python
def get_missing_fields(lead_dict, conv_dict=None) -> list[str]:
    # Priority: phone → route → travel dates → name → email
    # Returns list of human-readable missing field names
```

### L4. KB model (`app/models/kb.py`)
```python
class KBResult(BaseModel):
    entry_id: str
    title: str
    content: str
    score: float = 1.0
    source: str = "keyword"  # "keyword" | "vector" | "fallback"
```

---

## M. CONFIGURATION (`config/settings.py`)

### M1. All settings (with defaults)
```python
class Settings(BaseSettings):
    # API
    app_name: str = "BBC Chatbot API"
    debug: bool = False

    # Claude
    anthropic_api_key: str = ""          # empty = AI disabled, templates only
    claude_haiku_model: str = "claude-3-5-haiku-20241022"
    claude_sonnet_model: str = "claude-sonnet-4-20250514"
    claude_timeout: int = 8              # seconds

    # Supabase
    supabase_url: str                    # REQUIRED (no default)
    supabase_key: str                    # REQUIRED (no default)

    # Qdrant
    qdrant_url: str = ""                 # empty = disabled
    qdrant_api_key: str = ""
    qdrant_collection: str = "bbc_kb"
    qdrant_enabled: bool = False

    # Auth
    api_user: str = ""                   # empty = dev mode (no auth)
    api_pass: str = ""

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
    pipeline_timeout: int = 10           # seconds
    max_message_length: int = 2000
    max_messages_per_conversation: int = 50

    # CRM
    crm_api_url: str = "https://crm.buybusinessclass.com/ai"
    crm_api_token: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
```

### M2. Required vs optional

| Setting | Required | Consequence if empty |
|---------|----------|---------------------|
| `supabase_url` | YES | App won't start (Pydantic validation) |
| `supabase_key` | YES | App won't start |
| `anthropic_api_key` | No | AI calls fail → template fallback |
| `qdrant_url` | No | Vector search disabled |
| `api_user`/`api_pass` | No | Dev mode — admin endpoints unprotected |

---

## N. REQUIREMENTS & DEPLOYMENT

### N1. requirements.txt (10 packages)
```
fastapi==0.115.0
uvicorn[standard]==0.30.6
supabase==2.7.4
anthropic==0.34.2
pydantic==2.8.2
pydantic-settings==2.4.0
python-multipart==0.0.9
httpx==0.27.2
tenacity==9.0.0          ← UNUSED (imported nowhere)
python-dotenv==1.0.1
```

### N2. Dockerfile
```dockerfile
FROM python:3.11-slim AS deps
# Multi-stage: deps → production
# Non-root user: appuser
# HEALTHCHECK: curl http://localhost:8000/health every 30s
CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1
```
**Single worker** — no multiprocessing. Rate limiter's in-memory state stays consistent.

### N3. railway.toml
```toml
[build]
builder = "dockerfile"

[deploy]
healthcheckPath = "/health"
healthcheckTimeout = 10
restartPolicyType = "on_failure"
restartPolicyMaxRetries = 3
```

---

## O. TOOL EXECUTION (`app/tools/executor.py` — ~220 lines)

### O1. Architecture
```
Claude PROPOSES tool calls → Orchestrator VALIDATES via executor →
Orchestrator EXECUTES → Result returned to Claude
Claude NEVER calls tools directly
```

### O2. Security (OWASP LLM06 — Excessive Agency)
```python
MAX_TOOL_CALLS_PER_MESSAGE = 5
MAX_TOOL_COST_PER_MESSAGE = 0.10
MAX_AGENTIC_TIMEOUT_SECONDS = 30
```

### O3. 6-step validation in execute_tool()
```
1. Tool exists in registry? (reject unknown)
2. Tunnel allowed? (scope restriction)
3. Per-message call count limit? (max 5)
4. Budget limit? (max $0.10)
5. Validate parameters against JSON Schema (type, length, pattern, enum, range, SQL injection check)
6. Execute handler with try/except + latency logging
```

### O4. Current state
```python
TOOL_REGISTRY: dict[str, ToolDefinition] = {}
```
**Registry is EMPTY.** V1.5 foundation with zero tools. All infrastructure built, nothing registered.

---

## P. SERVICES

### P1. conversation_service.py (thin wrapper)
```python
async def get_or_create_conversation(...):
    return await db.get_or_create_conversation(...)

async def add_message(...):
    return await db.add_message(...)
```
Pure pass-through to supabase.py. No business logic.

### P2. lead_service.py
- `_calculate_score()` — Python-only scoring (NOT SQL trigger)
- `get_or_create_lead()` — finds existing or returns None
- `update_lead_from_entities()` — creates lead if doesn't exist + has useful data, updates conversation contact info, updates lead route/travel data, recalculates score

---

## Q. BUGS FOUND & FIXED

### Q1. Bug #1 — NameError on `history` (commit 01383c8)
**Root cause:** Steps 3.5 and 3.6 referenced `history` before it was fetched (history was only fetched at Step 6).
**Fix:** Added `history = await db.get_recent_messages(cid, limit=10)` immediately after saving user message, before Steps 3.5/3.6.

### Q2. Bug #2 — RouteCard ValidationError (commit 03ff2c4)
**Root cause:** `ChatResponse.route_card` was typed as `Optional[dict]`, but `generator.py` passed a `RouteCard` Pydantic model instance.
**Fix:** Changed type from `Optional[dict]` to `Optional["RouteCard"]`.

---

## R. KNOWN ISSUES & RECOMMENDATIONS

### R1. Security

| Issue | Severity | Location | Detail |
|-------|----------|----------|--------|
| Bearer V1 accepts ANY token | **HIGH** | `auth.py:77` | Any non-empty Bearer token = full admin. Must implement JWT validation. |
| Rate limiter resets on restart | MEDIUM | `rate_limiter.py` | In-memory only. Redeploy = all limits reset. Redis option in settings but unused. |
| Multi-turn probes unblocked | LOW | `orchestrator.py` Step 3.6 | Detection logs only, doesn't alter response. Consider blocking after threshold. |
| `max_message_length` setting unused | LOW | `settings.py` | Set to 2000, but `input_sanitizer.py` hardcodes its own `MAX_LENGTH = 2000`. |

### R2. Performance

| Issue | Impact | Location |
|-------|--------|----------|
| Dashboard fetches ALL rows | Scales poorly | `supabase.py:get_dashboard_stats()` |
| No Claude API retry | Single failure = template fallback | `claude.py:_call_model()` |
| `tenacity` unused | Dead dependency | `requirements.txt` |
| History fetched twice | Once at Step 1, once at Step 6 | `orchestrator.py` |

### R3. Architecture

| Issue | Detail |
|-------|--------|
| Tool registry empty | `executor.py` built and ready but unused |
| Agent check is placeholder | Step 2 always returns AI mode |
| `per_conversation_budget` unused | Setting exists but never checked |
| `crm_api_url`/`crm_api_token` unused | Settings defined but no CRM integration code |
| `redis_url` unused | Setting defined but no Redis integration |
| `budget_alert_threshold` unused | Setting defined but no alerting code |

### R4. Code quality

| Issue | Detail |
|-------|--------|
| `_run_sync` uses deprecated `get_event_loop()` | Should use `asyncio.get_running_loop()` |
| Some Supabase queries unparameterized search | `search` values in `.or_()` filters interpolated as f-strings |
| Conversation history re-fetched at Step 6 | Could reuse Step 1 history + new message |

---

## S. FILE SUMMARY TABLE

| File | Lines | Purpose |
|------|-------|---------|
| `app/main.py` | ~85 | FastAPI app, CORS, middleware, router wiring |
| `app/ai/claude.py` | ~130 | Anthropic API client (Haiku + Sonnet) |
| `app/ai/prompts.py` | ~165 | System prompts, KB sanitization |
| `app/ai/templates.py` | ~260 | 42 template keys, get_template() |
| `app/pipeline/orchestrator.py` | ~300 | 8-step pipeline brain |
| `app/pipeline/generator.py` | ~295 | Decision tree: template → Haiku → Sonnet → fallback |
| `app/pipeline/intent.py` | ~130 | 18 intents, 16 regex, Claude fallback |
| `app/pipeline/validator.py` | ~96 | Price/competitor/hallucination/XSS checks |
| `app/pipeline/entity_extractor.py` | ~290 | 9 entity types, 50 airports, 32 cities |
| `app/security/input_sanitizer.py` | ~120 | 29 injection patterns, 5-step sanitize |
| `app/security/rate_limiter.py` | ~100 | In-memory per-IP token bucket |
| `app/security/auth.py` | ~92 | Basic + Bearer auth |
| `app/security/request_logger.py` | ~25 | Request timing middleware |
| `app/db/supabase.py` | ~690 | ALL Supabase queries (8 tables) |
| `app/db/qdrant.py` | ~165 | Qdrant REST vector search |
| `app/tools/executor.py` | ~220 | Tool execution layer (empty registry) |
| `app/api/chat.py` | ~50 | POST /api/chat endpoint |
| `app/api/conversations.py` | ~50 | Admin conversations CRUD |
| `app/api/leads.py` | ~60 | Admin leads CRUD |
| `app/api/kb.py` | ~60 | Admin KB CRUD |
| `app/api/dashboard.py` | ~15 | Dashboard stats endpoint |
| `app/api/users.py` | ~40 | Admin users CRUD |
| `app/api/health.py` | ~45 | Health check endpoint |
| `app/models/chat.py` | ~30 | ChatRequest, ChatResponse, RouteCard |
| `app/models/admin.py` | ~210 | All admin Pydantic models |
| `app/models/kb.py` | ~12 | KBResult model |
| `app/models/lead.py` | ~55 | Lead tier/missing fields helpers |
| `app/services/conversation_service.py` | ~16 | Thin wrapper over supabase |
| `app/services/lead_service.py` | ~95 | Lead scoring + entity updates |
| `config/settings.py` | ~55 | Pydantic Settings from env |

**Total: ~3,840 lines of Python across 30 files.**
