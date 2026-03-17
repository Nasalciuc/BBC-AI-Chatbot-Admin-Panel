# BBC Chatbot API — CLAUDE.md

> Every line changes AI agent behavior.

## Identity

- **Project:** BBC AI Chatbot Backend (`bbc-chatbot-api`)
- **Purpose:** FastAPI — chat pipeline, admin CRUD, KB search, leads
- **NOT:** Frontend (`bbc-admin-app`), QM system, customer widget

## Current State (2026-03-16)

- Deploy: Railway (`admin-panel-error-production.up.railway.app`), BEHIND HEAD
- Chat pipeline WORKS: intent → entity → KB → template/Haiku/Sonnet
- GET /api/dashboard/stats WORKS
- Admin CRUD endpoints: conversations, leads, kb — shape aligned, auth dual (Basic+Bearer)
- Supabase: service_role REQUIRED. supabase-py is SYNC.

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

## Current Sprint (updated daily)
1. ✅ SDD governance (CLAUDE.md, 10 agents, 3 skills)
2. ✅ Auth dual-mode (Basic + Bearer)
3. ✅ Leads/Conversations/KB response shape fixes
4. ✅ Railway LIVE on correct repo (BBC-AI-Chatbot-Admin-Panel)
5. ✅ Widget Preview page created
6. 🔄 Vercel switching to correct repo
7. ⬜ Verify ALL pages show real data on production
8. ⬜ Dan demo (Dashboard + Leads + Conversations + KB + Widget)
9. ⬜ Qdrant semantic search connection
10. ⬜ System Prompt V2

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
