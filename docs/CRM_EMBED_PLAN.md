# CRM Embed — SSO Handshake Plan (prep only, no wiring)

Goal: run the admin panel (`bbc-admin-app`) **inside the company CRM** with one
login, on the same parent domain (`*.buybusinessclass.com`). The exact CRM
subdomain is unconfirmed, so everything below is parametrized. This document
describes the two SSO paths so we can pick one the moment the origin is confirmed.

Prerequisites already shipped (inert until switched on):
- **Auth hardened** (`app/security/auth.py`): Bearer JWT is the only real user
  path; role/tunnel_scope come from token claims. A shared/embedded session is
  now role-correct (no accidental `owner`).
- **Framing allowed** for BBC-owned origins via `vercel.json` CSP
  `frame-ancestors 'self' https://*.buybusinessclass.com https://buybusinessclass.com`.
- **CORS**: add the CRM origin to the `CORS_ORIGINS` env on Railway when confirmed
  (no code change).

---

## Option A — Same-domain reverse proxy (PREFERRED)

The CRM serves the panel under its own domain, e.g.
`crm.buybusinessclass.com/chat/*` → proxied to the Vercel app. Because the panel
then runs on the CRM's own origin, the existing cookie/JWT storage is shared
natively — no cross-origin token passing.

```
Browser                     CRM (crm.buybusinessclass.com)         Our app (Vercel)
   |  GET /chat/leads            |                                      |
   |---------------------------->|  reverse-proxy /chat/* -------------->|
   |                             |                                      |  serves panel
   |<------------------------------------------ panel HTML/JS ----------|
   |  panel calls API with the CRM session's JWT (same origin)          |
   |----------------------------> /api/leads  (Bearer <jwt>) ---------->|  backend
   |                             |                                      |  get_current_user
   |<------------------------------------------ 200 + role-scoped data -|
```

Pros: cleanest; no token bridge; cookies/JWT shared; CSP `frame-ancestors` already
allows it. Cons: needs the CRM team to set up the reverse proxy + path.

Remaining action: confirm the proxied path; ensure login issues a JWT the panel
reads from the shared origin.

---

## Option B — postMessage token injection (cross-domain fallback)

If no reverse proxy: the CRM logs the user into our backend (or mints a JWT signed
with the **same `JWT_SECRET`**), then posts it into the iframe. The panel stores it
for `api.ts` to attach as `Authorization: Bearer <jwt>`.

```
CRM page (crm.buybusinessclass.com)        iframe: panel (chat.buybusinessclass.com)
   | user already logged into CRM               |
   | mint/fetch JWT (same JWT_SECRET, claims:    |
   |   sub,email,name,role,tunnel_scope,exp)     |
   | iframe.postMessage({type:'bbc-auth',        |
   |    token}, 'https://chat.buybusinessclass.com') -------------------->|
   |                                             |  window.onmessage:
   |                                             |   verify e.origin is CRM
   |                                             |   store token → api.ts uses it
   |                                             |  GET /api/leads Bearer <jwt> --> backend
   |                                             |<-- 200 role-scoped
```

Security requirements (all satisfied by the hardened auth):
- The injected token MUST be a valid JWT (HS256, our `JWT_SECRET`) — the removed
  "any Bearer == API_PASS" fallback means a bogus token is rejected.
- The panel MUST validate `event.origin` against the confirmed CRM origin before
  accepting the token (implement at wiring time).
- CORS must include the CRM origin (Railway env).

Pros: works cross-domain, no proxy. Cons: token-bridge code + origin checks; JWT
must be shared/minted by the CRM.

---

## Decision checklist (Monday, no new code except Option B wiring)

- [ ] Confirm exact CRM origin (e.g. `crm.buybusinessclass.com`).
- [ ] Can the CRM reverse-proxy? → **Option A**. Else → **Option B**.
- [ ] Set `JWT_SECRET` on Railway (enables hardened auth) — shared with CRM if Option B.
- [ ] Add CRM origin to `CORS_ORIGINS` (Railway env).
- [ ] Optionally tighten `vercel.json` frame-ancestors to the exact CRM origin.
- [ ] Option B only: implement postMessage receiver + `event.origin` allowlist in the panel.
