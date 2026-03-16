# BBC Chatbot API — CLAUDE.md

> Every line in this file changes the behavior of an AI agent.

## Identity

- **Project:** BBC AI Chatbot Backend API (`bbc-chatbot-api`)
- **Purpose:** FastAPI backend — chat pipeline, admin CRUD, KB search, lead management
- **NOT:** Frontend (see `bbc-admin-app`), QM system, customer widget

## Current State (updated 2026-03-16)

- Deployed: `admin-panel-error-production.up.railway.app` (Railway, BEHIND HEAD)
- Chat pipeline WORKS: intent → entity → KB → template/Haiku/Sonnet → response
- Dashboard stats WORKS: GET /api/admin/stats
- Admin CRUD endpoints DO NOT EXIST yet
- Supabase: service_role key REQUIRED. supabase-py is SYNCHRONOUS.

## Stack

Python 3.11 | FastAPI async | supabase-py (sync — wrap everything) | Claude Haiku + Sonnet | Qdrant Cloud (1536d, cosine) | Railway

## Critical Rules

1. EVERY supabase call in async endpoint: `await asyncio.to_thread(lambda: sb.table(...).execute())`
2. SUPABASE_KEY = service_role JWT. Anon key = RLS 42501 errors.
3. NEVER pass SQL expressions to supabase-py — `"now()"` sent as literal. Use `datetime.utcnow().isoformat()`
4. ALL admin endpoints return `{ success: bool, data: T, count: int, error?: str }`
5. EVERY AI call logs cost to pipeline_runs table.

## File Structure

app/main.py | app/db/supabase.py (ALL db) | app/pipeline/ (8-step chat) | app/ai/ (claude, prompts, templates) | app/models/ (Pydantic) | app/routes/ (endpoints)

## Never List

1. NEVER anon key
2. NEVER sync supabase in async
3. NEVER raw LLM output to frontend
4. NEVER log PII at INFO
5. NEVER hardcode keys
6. NEVER skip cost tracking
7. NEVER change response shape without frontend types update
8. NEVER .rpc() for simple CRUD

## Gates

pytest passes | no hardcoded keys | async wrapping | response shape correct | cost tracked | /health 200
