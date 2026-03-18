# BBC Chatbot API — CLAUDE.md

> Every line changes AI agent behavior.

## Identity

- **Project:** BBC AI Chatbot Backend (`bbc-chatbot-api`)
- **Purpose:** FastAPI — chat pipeline, admin CRUD, KB search, leads
- **NOT:** Frontend (`bbc-admin-app`), QM system, customer widget

## Current State (2026-03-18)

- Deploy: Railway LIVE at HEAD, auto-deploy ON, Trial plan (~20 days remaining)
- Pipeline: 8 steps + 3 security sub-steps (3.5 handoff, 3.6 probe, 7.5 refusal)
- Qdrant: CONNECTED, MiniLM 384d server-side FREE, 30 entries, feature flag ON
- Templates: 41+ keys (~90% coverage)
- Intents: 22 (14 original + 8 support V2)
- Security: 29 injection patterns, KB sanitization, history sanitization, XSS strip, Sonnet DoW cap
- Summarization: every 5 messages (Haiku)
- Tools: executor.py V2 foundation (zero tools active)
- ThreadPool: 20 workers (upgraded from 5)

## Stack

Python 3.11 | FastAPI async | supabase-py (sync — wrap) | Claude Haiku+Sonnet | Qdrant Cloud | Railway

## Critical Rules

1. EVERY supabase in async: `await asyncio.to_thread(lambda: sb.table(...).execute())`
2. SUPABASE_KEY = service_role JWT. Anon = 42501.
3. NEVER SQL expressions as values. Python `datetime.utcnow().isoformat()`
4. ALL admin endpoints: `{ success: bool, data: T, count: int, error?: str }`
5. EVERY AI call logs cost.

## File Structure

app/main.py | app/db/supabase.py | app/pipeline/(orchestrator,intent,entity,generator,lead_service) | app/ai/(claude,prompts,templates) | app/models/ | app/routes/

## Completed Work
- Sprint 1 (5 days): 20 deliverables — infra, widget, Qdrant, drawer, prompts, templates, docs
- Week 2 (in progress): support KB expansion (30 entries), support intents (8), CSV export, Gold KPI, security hardening (S1-S3), handoff mechanism
- Security: 4-layer defense (sanitizer 29 patterns + system prompt + validator + budget guard) + V2 tool executor foundation

## Next Actions
1. ⬜ Railway Trial → Hobby (Dan — $5/mo, ~20 days remaining)
2. ⬜ Widget embed on buybusinessclass.com (Dan — instructions in docs/WIDGET-EMBED-GUIDE.md)
3. ⬜ UptimeRobot monitoring (/health every 5 min)
4. ⬜ Post-launch: iterate based on real pipeline_runs data

## Git Rules
- One scope per commit: feat(api), fix(ui), fix(infra), docs
- NEVER mix frontend + backend in one commit
- ONLY remote: github.com/Nasalciuc/BBC-AI-Chatbot-Admin-Panel
- Push after EVERY completed task, verify on production

## Never List

1. NEVER anon key | 2. NEVER sync in async | 3. NEVER raw LLM output
4. NEVER PII at INFO | 5. NEVER hardcode keys | 6. NEVER skip cost tracking
7. NEVER change shape without frontend update | 8. NEVER .rpc() for CRUD

## Gates

pytest | no hardcoded keys | async wrapped | shape correct | cost tracked | /health 200

## Agent Teams

Status: **ACTIVE**. Workflow: research → plan → build. Contract-first. Budget: $50/day sprint.
