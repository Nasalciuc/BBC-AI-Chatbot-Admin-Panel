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

If no reverse proxy: the CRM's **backend** calls our bridge endpoint
(`POST /api/auth/crm-bridge`) after its own login to mint a panel JWT for the
operator, then its frontend posts that token into the iframe. The panel stores it
for `api.ts` to attach as `Authorization: Bearer <jwt>`.

> **Why a bridge endpoint, not a shared `JWT_SECRET`?** We do NOT hand `JWT_SECRET`
> to the CRM. The fewer places a signing secret lives, the smaller the blast radius.
> Instead the CRM authenticates to a dedicated endpoint with `CRM_BRIDGE_SECRET`
> (server-to-server only, constant-time compared) and receives a normal JWT minted
> by us — the same claims/expiry as `/api/auth/login`. This mirrors the existing
> `CRON_SECRET` pattern. `CRM_BRIDGE_SECRET` must never equal `JWT_SECRET` and must
> never reach client-side code.

```
CRM page (crm.buybusinessclass.com)        iframe: panel (chat.buybusinessclass.com)
   | user already logged into CRM               |
   | CRM backend → POST /api/auth/crm-bridge     |
   |   Authorization: Bearer <CRM_BRIDGE_SECRET> |
   |   { "email": "operator@..." }               |
   |   ← 200 { token (our JWT), user }           |
   | iframe.postMessage({type:'bbc-auth',        |
   |    token}, 'https://chat.buybusinessclass.com') -------------------->|
   |                                             |  window.onmessage:
   |                                             |   verify e.origin is CRM
   |                                             |   store token → api.ts uses it
   |                                             |  GET /api/leads Bearer <jwt> --> backend
   |                                             |<-- 200 role-scoped
```

Security requirements (all satisfied by the hardened auth):
- The token is minted by US via the bridge (HS256, our `JWT_SECRET`) — the CRM
  never sees or signs with `JWT_SECRET`; it only holds `CRM_BRIDGE_SECRET`.
- The bridge is server-to-server, gated by `CRM_BRIDGE_SECRET` (constant-time),
  rate-limited, and audit-logged on every call (success and failure). Unknown
  emails get a generic 401 (no operator-email enumeration).
- The panel validates `event.origin` (BBC domains + `VITE_CRM_ORIGINS`) before
  accepting the token — see `crm-embed-auth.ts`.
- CORS must include the CRM origin (Railway env) for the panel's own API calls;
  the bridge itself is server-to-server, so CORS does not apply to it.

Pros: works cross-domain, no proxy, `JWT_SECRET` stays with us. Cons: CRM must
hold a dedicated `CRM_BRIDGE_SECRET` and make one server-to-server call.

---

## CRM-side integration (after their own login)

1. Server-to-server, from the CRM's backend (never the browser), call:
   ```
   POST https://<panel-api-domain>/api/auth/crm-bridge
   Authorization: Bearer <CRM_BRIDGE_SECRET>   (given to you out-of-band, NOT the panel's JWT_SECRET)
   Content-Type: application/json

   { "email": "operator@buybusinessclass.com" }

   → 200 { "token": "<jwt>", "user": { ... } }
   ```

2. In the CRM's frontend, once the panel iframe has loaded, send that token in:
   ```js
   iframe.contentWindow.postMessage(
     { type: 'bbc-auth', token: '<jwt from step 1>' },
     'https://<panel-domain>'   // e.g. https://chat.buybusinessclass.com
   )
   ```

The panel already listens for this and logs the operator in automatically — no
further integration needed on our side.

---

## Option B — receiver (shipped)

Panel listens for `postMessage` (`bbc-admin-app/src/lib/crm-embed-auth.ts`).
Allowed origins: `https://buybusinessclass.com`, `https://*.buybusinessclass.com`,
`localhost`, plus extras from `VITE_CRM_ORIGINS`.

### Dan — widget (visitor chat in CRM)

```html
<iframe
  src="https://chat.buybusinessclass.com/widget-embed?embedded=1"
  width="420" height="640"
  style="border:none;border-radius:12px;"></iframe>
```

### Dan — operator panel in CRM (full admin app)

```html
<iframe
  id="bbc-ops"
  src="https://chat.buybusinessclass.com/"
  width="100%" height="100%"
  style="border:none;"></iframe>
<script>
  // After CRM login: CRM backend fetches a JWT from POST /api/auth/crm-bridge
  // (Authorization: Bearer <CRM_BRIDGE_SECRET>) — NOT minted with JWT_SECRET.
  // See "CRM-side integration" above. CRM_ISSUED_JWT is that returned token.
  const iframe = document.getElementById('bbc-ops')
  iframe.addEventListener('load', () => {
    iframe.contentWindow.postMessage(
      { type: 'bbc-auth', token: CRM_ISSUED_JWT },
      'https://chat.buybusinessclass.com'
    )
  })
  window.addEventListener('message', (e) => {
    if (e.data?.type === 'bbc-auth-ack') console.log('BBC auth', e.data.ok)
  })
</script>
```

## Decision checklist

- [ ] Confirm exact CRM origin (e.g. `crm.buybusinessclass.com`).
- [ ] Can the CRM reverse-proxy? → **Option A**. Else → **Option B** (bridge + receiver ready).
- [ ] Set `JWT_SECRET` on Railway — stays with us, NOT shared with the CRM.
- [ ] Set `CRM_BRIDGE_SECRET` on Railway (dedicated random secret; share with the
      CRM team out-of-band). Required for Option B.
- [ ] Add CRM origin to `CORS_ORIGINS` (Railway env).
- [ ] Optionally tighten `vercel.json` frame-ancestors to the exact CRM origin.
- [x] Option B: bridge endpoint `POST /api/auth/crm-bridge` (server-to-server mint).
- [x] Option B: postMessage receiver + origin allowlist in the panel.
