# BBC Chatbot API — CLAUDE.md

> Governance file for Claude Code. Read automatically on session start.
> Last updated: 2026-03-16

## Project Identity

| Field | Value |
|-------|-------|
| Name | BBC Chatbot API (BuyBusinessClass) |
| Type | AI chatbot backend + admin API |
| Repo | `bbc-chatbot-api` |
| Companion Frontend | `bbc-admin-app` (React) |
| Deploy | Railway (Docker) |

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | FastAPI 0.115 + Uvicorn |
| Language | Python 3.9+ |
| Database | Supabase (PostgreSQL) |
| Vector Search | Qdrant (optional) |
| Cache / Rate Limit | Redis (optional) |
| LLM | Anthropic Claude (Haiku 3.5, Sonnet 4) |
| Auth | HTTP Basic Auth (dev mode bypass) |
| Validation | Pydantic 2.8 + BaseSettings |
| HTTP Client | httpx |
| Retries | tenacity |
| Config | python-dotenv + `config/settings.py` |

## Architecture Rules

### Directory Layout

```
app/
├── __init__.py
├── main.py              # FastAPI app, middleware, router includes
├── ai/                  # AI/LLM utilities
├── api/                 # Route handlers (chat, conversations, leads, kb, dashboard, health)
├── db/                  # Database client (Supabase)
├── models/              # Pydantic request/response schemas
├── pipeline/            # AI pipeline (orchestrator, claude, intent, generator, entity_extractor, validator)
├── security/            # Auth, input sanitizer, rate limiter, request logger
├── services/            # Business logic (conversation_service, lead_service)
└── utils/               # Shared utilities
config/
├── settings.py          # Pydantic BaseSettings (env-driven)
migrations/              # SQL migration files (schema, functions, seed)
tests/                   # pytest test suite
specs/                   # SDD specification files
```

### Key Conventions

1. **Route → Service → DB** — routes call services, services call DB. No direct DB access from routes.
2. **Pydantic everywhere** — all request/response bodies use Pydantic models in `app/models/`.
3. **Settings via BaseSettings** — all config from env vars through `config/settings.py`. Never hard-code secrets.
4. **Auth on all endpoints** — except `/health`. Use `verify_credentials` dependency.
5. **Input sanitisation** — all user text passes through `app/security/input_sanitizer.py`.
6. **Rate limiting** — enforced via `app/security/rate_limiter.py` (token bucket, Redis-backed when available).
7. **Async first** — all route handlers and service methods should be `async def`.
8. **Response envelope** — all admin endpoints return `{ success, data, count, error }`.

### AI Pipeline

```
User message
  → InputSanitizer (clean + detect suspicious patterns)
  → RateLimiter (check burst/sustained/daily limits)
  → Orchestrator
      → IntentClassifier (classify user intent)
      → EntityExtractor (pull dates, destinations, etc.)
      → Generator (call Claude API with context)
      → Validator (check response quality)
  → Response
```

### Security Layers

| Layer | File | Purpose |
|-------|------|---------|
| Auth | `app/security/auth.py` | HTTP Basic with constant-time comparison |
| Sanitise | `app/security/input_sanitizer.py` | Strip injection, detect abuse |
| Rate limit | `app/security/rate_limiter.py` | Burst 15, sustained 3/s, daily 300 |
| Request log | `app/security/request_logger.py` | Audit trail middleware |

### Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `SUPABASE_URL` | Yes | Database URL |
| `SUPABASE_KEY` | Yes | Database service key |
| `ANTHROPIC_API_KEY` | Yes | Claude API key |
| `API_USER` | No (dev) | HTTP Basic username |
| `API_PASS` | No (dev) | HTTP Basic password |
| `QDRANT_URL` | No | Vector DB URL |
| `REDIS_URL` | No | Cache/rate limiter URL |

> **Never commit `.env` files.**

### Budget Controls

| Setting | Default |
|---------|---------|
| `DAILY_BUDGET` | $50 |
| `PER_CONVERSATION_BUDGET` | $0.50 |
| Haiku model | `claude-3-5-haiku-20241022` |
| Sonnet model | `claude-sonnet-4-20250514` |

## Commands

| Task | Command |
|------|---------|
| Run server | `uvicorn app.main:app --reload` |
| Run tests | `pytest` |
| Run single test | `pytest tests/test_<name>.py -v` |
| Install deps | `pip install -r requirements.txt` |
| Docker build | `docker build -t bbc-chatbot-api .` |
| Docker compose | `docker-compose up` |

## Quality Gates

Before any PR:

1. `pytest` — all tests must pass
2. No hard-coded secrets or API keys
3. All new endpoints must have Pydantic request/response models
4. All user-facing text must pass through input sanitiser
5. New admin endpoints must follow the response envelope format

## Spec-Driven Development

- Specs live in `specs/` — one Markdown file per complex feature.
- `specs/api-contract.md` is the source of truth for admin endpoint shapes.
- Frontend `src/lib/types.ts` must mirror these shapes.
- Claude must read the relevant spec before implementing a feature.

## Skills

| Skill | Purpose |
|-------|---------|
| `bbc-admin-endpoint` | Pattern for adding new admin CRUD endpoints |

## Do NOT

- Add dependencies without explicit approval.
- Commit `.env` or any secrets.
- Skip input sanitisation on user-provided text.
- Use synchronous DB calls — always `async`.
- Bypass rate limiting for any public endpoint.
- Return raw database errors to the client.
- Use `print()` for logging — use Python `logging` module.
