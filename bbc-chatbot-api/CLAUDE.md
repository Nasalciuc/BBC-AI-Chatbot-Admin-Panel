# BBC Chatbot API — CLAUDE.md

> Regulile de cod sunt în [`../CLAUDE.md`](../CLAUDE.md) și sunt verificate de teste, nu de bunăvoință.

## Identity

- **Project:** BBC AI Chatbot Backend (`bbc-chatbot-api`)
- **Purpose:** FastAPI — chat pipeline, admin CRUD, KB search, leads
- **NOT:** Frontend (`bbc-admin-app`), QM system, customer widget (`bbc-widget`)

## Stack

Python 3.11 (Railway) | FastAPI async | supabase-py (sync — wrap) | Claude Haiku + Sonnet + Opus | Qdrant Cloud | Railway

## Critical Rules

1. NICIUN apel supabase sincron pe event loop. Stratul de DB trece prin
   `await _run_sync(fn, idempotent=...)` (`app/db/supabase.py`). `idempotent=False`
   la FIECARE insert: o deconectare poate cădea DUPĂ ce serverul a scris, iar un
   retry ar dubla rândul (un mesaj dublat otrăvește istoricul, sumarizarea și KPI-urile).
2. `SUPABASE_KEY` = service_role JWT. Cheia anon dă 42501.
3. NICIODATĂ expresii SQL ca valori. Timpul se calculează în Python:
   `datetime.now(timezone.utc).isoformat()`.
4. TOATE endpointurile admin întorc `{ success: bool, data: T, count: int, error?: str }`.
5. FIECARE apel AI își loghează costul.
6. `.rpc()` doar unde PostgREST nu poate exprima operația atomic (două locuri în
   `supabase.py`) — niciodată pentru CRUD obișnuit.
7. NICIODATĂ PII (email, telefon, nume) în loguri la INFO sau mai sus.

## File Structure

`app/main.py` · `app/api/` (rute) · `app/db/supabase.py` · `app/pipeline/`
(orchestrator, intent, entity_extractor, generator, corrections) ·
`app/services/` (lead_service, crm, routing, handoff, closing) ·
`app/ai/` (claude, prompts, templates) · `app/security/` · `app/models/` ·
`migrations/` · `tests/`

## Git Rules

- Un singur scope per commit: `feat(api)`, `fix(ui)`, `fix(infra)`, `docs`
- NICIODATĂ frontend + backend în același commit
- Singurul remote: github.com/Nasalciuc/BBC-AI-Chatbot-Admin-Panel

## Gates

`pytest` verde (inclusiv `tests/test_code_discipline.py`) · fără chei hardcodate ·
apeluri DB wrapped · shape corect · cost logat · `/health` 200

## Migrații

Fișierul în repo NU înseamnă aplicat. Fiecare PR care adaugă o migrație declară
`APPLIED: da/nu`, iar codul care depinde de o coloană nouă tratează absența ei ca
pe un caz normal, nu ca pe o excepție.

---

## Istoric (neactualizat, 18 mar 2026)

Ce urmează a fost adevărat în martie 2026 și NU a mai fost verificat de atunci.
Nu lua nicio decizie pe baza lui — citește codul. Starea sistemului nu se mai
documentează aici, tocmai pentru că îmbătrânește și devine minciună.

- Deploy: Railway LIVE at HEAD, auto-deploy ON, Trial plan (~20 days remaining)
- Pipeline: 8 steps + 3 security sub-steps (3.5 handoff, 3.6 probe, 7.5 refusal)
- Qdrant: CONNECTED, MiniLM 384d server-side FREE, 30 entries, feature flag ON
- Templates: 41+ keys (~90% coverage) · Intents: 22 (14 original + 8 support V2)
- Security: 29 injection patterns, KB sanitization, history sanitization, XSS strip
- Summarization: every 5 messages (Haiku) · Tools: executor.py V2, zero tools active
- ThreadPool: 20 workers
- Next actions din martie: Railway Trial → Hobby, widget embed pe
  buybusinessclass.com, UptimeRobot pe `/health`
