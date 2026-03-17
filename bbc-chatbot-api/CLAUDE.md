# BBC Chatbot API — CLAUDE.md

> Every line changes AI agent behavior.

## Identity

- **Project:** BBC AI Chatbot Backend (`bbc-chatbot-api`)
- **Purpose:** FastAPI — chat pipeline, admin CRUD, KB search, leads
- **NOT:** Frontend (`bbc-admin-app`), QM system, customer widget

## Current State (2026-03-17)

- Deploy: Railway LIVE at HEAD, auto-deploy ON
- Chat pipeline WORKS: intent → entity → KB → template/Haiku/Sonnet
- Qdrant: CONNECTED, 384d MiniLM, 15 entries, feature flag ON
- Templates: 33 keys
- Summarization: every 5 messages
- Admin CRUD endpoints: conversations, leads, kb, users — shape aligned, auth dual (Basic+Bearer)
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

## Current Sprint (completed 2026-03-17 to 2026-03-21)
1. ✅ SDD governance (CLAUDE.md, 10 agents, 3 skills, Agent Teams)
2. ✅ Auth dual-mode (Basic + Bearer), 97/97 tests
3. ✅ Response shape fixes (leads, conversations, KB)
4. ✅ Railway LIVE — correct repo, auto-deploy, Hobby plan pending
5. ✅ Vercel LIVE — SPA routing, npm build, iframe headers
6. ✅ Widget Preview + Widget Embed (/widget-embed for iframe)
7. ✅ CORS updated for buybusinessclass.com
8. ✅ Qdrant semantic search — MiniLM 384d FREE, 15 entries
9. ✅ Lead detail drawer — click row → Sheet with conversation
10. ✅ Templates 23→33 keys, auto-summarization every 5 msgs
11. ✅ System Prompt V2 — few-shot, handoff, premium tone
12. ✅ KB gap analysis script + README rewrite + user guide

## Next Sprint (Week 2)
1. ⬜ Widget pe buybusinessclass.com (Dan decision)
2. ⬜ Railway Hobby upgrade (Dan — $5/mo)
3. ⬜ Users page frontend (mock → real)
4. ⬜ Dashboard polish (real data styling)
5. ⬜ WhatsApp integration (Meta verification)

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
