# CRM Integration Readiness — Report

**Date:** 2026-07-12  
**Branch:** `fix/handoff-hole-silent-reservation` (working tree; handoff fix already committed)  
**Git rule respected:** NO commits/staging — all changes are uncommitted working-tree edits.

---

## 1. Phase A answers (anchors verified on HEAD)

### A1 — `get_current_user` original body
`app/security/auth.py` lines 24–114. Matched byte-for-byte. Original flow:
dev-mode allow-all → Basic Auth → Bearer (JWT decode *if* jwt_secret, else fall
through) → `Bearer == API_PASS → owner` fallback → 401.

### A2 — The three unsafe fallbacks (verbatim, all present on HEAD)
1. `if not settings.api_user or not settings.api_pass: return {"id": "dev", "role": "owner", "name": "dev"}` (silent allow-all).
2. `if settings.api_pass and secrets.compare_digest(token, settings.api_pass): return {"id": "admin", "role": "owner", "name": "admin"}` (any Bearer == API_PASS → owner).
3. `if settings.jwt_secret:` wrapper gating the JWT decode (skipped entirely when unset).

### A3 — JWT claims at login (`auth_routes._issue_jwt`)
Confirmed: `sub, email, name, role, tunnel_scope, avatar_url, phone, exp`
(signed HS256 with `settings.jwt_secret`). Hardened validation accepts exactly
these — login flow and claims **unchanged**.

### A4 — `vercel.json` original
`/widget-embed` → CSP `frame-ancestors 'self' https://buybusinessclass.com https://*.buybusinessclass.com http://localhost:*` + `X-Frame-Options: ALLOWALL`.
`/((?!widget-embed).*)` → `X-Frame-Options: DENY` (blocks all framing).

### A5 — Auth tests + conventions
Only `tests/test_leads_api.py` touches auth: sets `API_USER`/`API_PASS` via
`os.environ` at import, uses **Basic Auth** header (`base64(user:pass)`), asserts
200 with creds / 401 without. New tests build JWTs with `jwt.encode(payload,
settings.jwt_secret, "HS256")` and patch `settings` via `patch.object`.

### A6 — CRM-embed / frame-ancestor field in settings?
**NO** dedicated field. CORS is env-driven (`CORS_ORIGINS` → `cors_origins_env`
→ merged with `REQUIRED_CORS_ORIGINS`). No code change needed to add the CRM
origin — env only. (We did not add a new settings field; env-driven is cleaner.)

**STOP conditions:** none triggered — auth had owner-fallbacks, vercel.json was
DENY (not parametrized), and login DOES issue role/tunnel_scope JWTs.

---

## 2. Changes (diff stat)

```
 bbc-admin-app/vercel.json               |   4 +-
 bbc-chatbot-api/.env.example            |   9 ++-
 bbc-chatbot-api/app/security/auth.py    | 117 +++++++++++++++++----------
 bbc-chatbot-api/config/settings.py      |   4 +-
 bbc-chatbot-api/tests/test_leads_api.py |   9 ++-
 + new: bbc-chatbot-api/tests/test_auth_hardening.py
 + new: docs/CRM_EMBED_PLAN.md
 + new: docs/reports/CRM_INTEGRATION_READY_REPORT.md
```

### 3 fallbacks removed (`auth.py`)
1. ✅ Silent allow-all (`api_user/api_pass empty → owner`) → replaced by an
   **explicit** `debug and not jwt_secret` dev bypass (logged, never silent).
2. ✅ `Bearer == API_PASS → owner` → **deleted**. A Bearer token must be a valid JWT.
3. ✅ `if settings.jwt_secret:` wrapper → **removed**. JWT decode is unconditional;
   the Bearer path **fails closed (500)** if `jwt_secret` is empty.

Basic Auth kept as an **ops escape hatch**, gated on `api_user && api_pass`,
returning owner + `tunnel_scope: all`. Docstring rewritten to the new contract.

### `settings.py`
`jwt_secret` comment: `# REQUIRED in production; the Bearer path fails closed
(500) if empty and debug=False. Do NOT hard-crash on import — Railway needs /health.`
Value stays `""` (no import-time crash).

### `vercel.json`
Non-widget-embed route: `X-Frame-Options: DENY` → CSP
`frame-ancestors 'self' https://*.buybusinessclass.com https://buybusinessclass.com`.
Framing allowed ONLY from BBC-owned origins (covers the CRM subdomain without
knowing it yet); blocked for everyone else. `X-Frame-Options` can't express a
wildcard subdomain, and modern browsers honor CSP `frame-ancestors` over it —
hence the switch. `/widget-embed` rule untouched.

### `.env.example`
- `JWT_SECRET=...` added with the "required in prod / fails closed" note.
- `API_USER/API_PASS` recommented as optional Basic escape hatch.
- CORS block: note to append `https://crm.buybusinessclass.com` (Railway env) once confirmed.

---

## 3. Test & build results

- **`test_auth_hardening.py` (new): 12/12 PASS** — valid JWT keeps role (not owner); qa role preserved; Bearer==API_PASS → 401; no-secret+prod+Bearer → 500; debug bypass → owner; bypass inactive when secret set; expired → 401; malformed → 401; wrong-secret → 401; login-issued token still works (guardrail); Basic escape hatch works; no-auth → 401.
- **`test_leads_api.py`: PASS** — updated `test_list_leads_401` to patch `debug=False` (the hardened dev-bypass semantics changed from api_user/api_pass-empty to debug+no-secret). Guardrail 401 path exercised under the production contract.
- **Full suite: 162 passed, 3 failed.** The 3 are pre-existing `test_generator.py` failures (2 SmartRouting stable; 1 BudgetGuard passes in isolation = order pollution) — unrelated to this work, present before it.
- **`bbc-admin-app` build: PASS**, 0 TypeScript errors (chunk-size warning is pre-existing).
- Linters on edited files: clean.

**Current logins still work (no lockout):** `test_login_issued_token_still_works`
builds a token via the real `_issue_jwt` and passes it through the hardened
`get_current_user` → correct id/email/role/tunnel_scope. Login flow and JWT
claims were not touched.

---

## 4. Monday checklist — NO new code required

- [ ] Set `JWT_SECRET` on Railway (if not already) — enables the hardened JWT path in production.
- [ ] Add the CRM origin to `CORS_ORIGINS` on Railway once confirmed (e.g. `https://crm.buybusinessclass.com`) — dashboard env, no code deploy.
- [ ] (Optional) Tighten `vercel.json` `frame-ancestors` from `*.buybusinessclass.com` to the exact CRM origin — one-line change.
- [ ] Pick SSO path per `docs/CRM_EMBED_PLAN.md` (Option A same-domain proxy preferred; Option B postMessage otherwise). Only Option B needs new code (postMessage receiver + origin allowlist).

---

## 5. Risk notes — does hardening lock anyone out?

- **Cron** (`/api/cron/*`): uses its own `CRON_SECRET` Bearer check in `cron.py` (line 139–143) — **does NOT** route through `get_current_user`. Unaffected.
- **Chat** (`/api/chat`): public router, no auth dependency. Unaffected.
- **Widget**: hits public chat/SSE endpoints only. Unaffected.
- **Admin panel**: uses login-issued JWTs (verified by guardrail test). Works.
- **Basic-auth ops/tools**: still work when `API_USER`/`API_PASS` are set.
- **The one behavior change to be aware of:** with `DEBUG=false` and no
  `JWT_SECRET`, admin Bearer requests now return **500** (was: silently accepted
  as owner). This is the intended fix. Action: ensure `JWT_SECRET` is set on
  Railway (checklist item 1) — it already must be, since login issues JWTs with it.

**Local `.env` note:** the dev `.env` has `DEBUG=true` and no `JWT_SECRET`, so
locally the explicit dev bypass returns owner (as intended for development).
Production has `DEBUG=false`, so the hardened JWT path is authoritative there.

---

## 6. Open items

1. **`JWT_SECRET` on Railway** must be confirmed present before/at merge (else admin panel gets 500 in prod). Highest-priority verification.
2. Exact CRM subdomain (Monday) → CORS env + optional CSP tightening.
3. SSO path decision (A vs B); Option B needs a small postMessage-receiver PR later.
4. `test_generator.py` pre-existing failures — separate cleanup ticket.
5. Everything here is **inert until switched on** (framing allows BBC origins but nothing embeds yet; auth already expects JWT which login already provides). Safe to merge.
