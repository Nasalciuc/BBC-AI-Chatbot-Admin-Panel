# Chatbot SSO — CRM ↔ chat.buybusinessclass.com

Auto-login for the embedded chatbot: if a user is logged into the CRM, they are
authenticated in the chatbot automatically, with no second login.

This document is the integration spec for the **chatbot backend** developer.

---

## 1. How the authenticated user arrives

When a user opens `/chat` in the CRM, the page loads the chatbot in an iframe
with a signed token on the URL:

```
https://chat.buybusinessclass.com/?sso_token=<JWT>
```

> **Param rename (action for the CRM team):** the SSO param is now
> `sso_token`. The legacy `?token=` spelling keeps working everywhere
> EXCEPT public token routes (`/set-password` — operator invite links own
> `?token=` there). Please switch the embed URL to `?sso_token=` when
> convenient; until then the legacy spelling continues to log in.

The chatbot frontend reads `sso_token` (or legacy `token`) from the
**query string** on page load.

---

## 2. JWT contract (what the CRM sends)

|                   |                                                               |
| ----------------- | ------------------------------------------------------------- |
| **Algorithm**     | `HS256`                                                       |
| **Shared secret** | `CHAT_SSO_SECRET` (delivered separately, securely — see §4)   |
| **Transport**     | query param `?sso_token=` (legacy `?token=` still accepted)   |
| **TTL**           | 900s (15 min) — short-lived, used only for login at load time |

**Payload (claims):**

```json
{
    "iss": "crm",
    "sub": "123",
    "name": "John Doe",
    "iat": 1753190000,
    "nbf": 1753189995,
    "exp": 1753190900
}
```

-   `iss` — always `"crm"`.
-   `sub` — CRM user id (string).
-   `name` — the logged-in user's display name. **This is the only user data sent.**
-   `iat` / `nbf` / `exp` — standard timestamps (`nbf = iat - 5s` leeway, `exp = iat + 900s`).

> Only `name` is sent today. If you also need email / role, ask the CRM side to
> add them to the payload — it's a one-line change.

---

## 3. What the chatbot backend must do

1. Read `token` from the query string.
2. Verify the signature with the **same secret** (`CHAT_SSO_SECRET`) and algorithm `HS256`.
3. Validate `exp` (and optionally `nbf` and `iss === "crm"`).
4. If valid → read `name` and log in / create the user's session automatically.
5. If missing / invalid / expired → fall back to the normal flow (guest / manual login).

---

## 4. BBC panel implementation (this repo)

This spec is implemented as an **exchange** flow (the panel frontend is a SPA on a
different origin than the API, so the token is read client-side and exchanged
server-side):

- **Frontend** (`bbc-admin-app/src/lib/crm-embed-auth.ts`): on load, reads
  `?sso_token=` (or legacy `?token=` outside public token routes) from the
  URL, POSTs it to the backend, applies the returned BBC session on success,
  then strips the param from the URL — on success only; a failed exchange
  leaves the URL intact.
- **Backend** (`POST /api/auth/sso/crm-exchange`): verifies the CRM JWT with
  `CHAT_SSO_SECRET` (HS256, `exp`/`iat` required, `iss=crm`), looks up the BBC
  user **by email**, and issues our own normal session JWT.
- **Security:** `role` / `tunnel_scope` are ALWAYS looked up fresh from the BBC
  `users` table — never taken from the CRM token.

> **Known gap:** the CRM payload above has no `email` yet (only `name` + CRM
> `sub`). We key BBC users by email, so auto-login **fails closed with a clear
> error until the CRM team adds `email`** to the payload (their doc notes this is
> a one-line change). Everything else works end-to-end the moment `email` lands.

`CHAT_SSO_SECRET` must be **identical** on both sides (the CRM signs, we verify)
and shared over a secure channel.

---

## 6. Agent presence gate (`POST /api/integration/agent-presence`)

The CRM asks us one question before letting a sales agent work leads:
**is this agent genuinely present in the chat panel?** Live visitors are routed
only to agents whose panel is open and who pressed **Ready** — an agent sitting
in the CRM with the chat closed leaves visitors waiting for nobody.

We answer with **state only**. No name, no phone, no team, no conversation
counts. The endpoint performs one read and writes nothing.

> **Call this from the CRM's BACKEND.** The shared secret signs the token; it
> must never reach a browser.

### Request

```
POST /api/integration/agent-presence
Content-Type: application/json

{"token": "<JWT>"}
```

The token is signed with the same secret as the SSO login token, but it is
**not the same token** — see the box below.

| | |
|---|---|
| Algorithm | `HS256`, signed with `CHAT_SSO_SECRET` |
| Required claims | `iss="crm"`, `iat`, `exp`, **`purpose="presence"`** |
| TTL | **≤ 60 seconds** — it is minted per check |
| Max age | `iat` older than **120s** is refused even if `exp` is still valid |
| Agent identity | `email` in the payload (`sub` is used only if it *is* an address) |

> ### ⚠️ `purpose` is mandatory — action for the CRM team
>
> Both endpoints verify with `CHAT_SSO_SECRET`. Without an audience claim,
> every presence token — one per lead-open, high frequency, passing through
> your logs, an APM span, a proxy, a retry queue — would also be a **full
> login credential** for that agent's panel account. Anything that merely
> *observed* one could exchange it for a session.
>
> - Presence tokens **must** carry `"purpose": "presence"` (or `"aud"`).
>   Without it the answer is `401`, and the gate fails open.
> - Login tokens must carry `"purpose": "login"` or **omit the claim**
>   (today's shape — it keeps working unchanged). A presence token sent to
>   `/sso/crm-exchange` is refused.
>
> Keep the signing host **NTP-synced**: an `iat` in the future is rejected
> with no leeway, exactly as the login exchange has always rejected it.

The email is read from the **signed payload only** — never from a query string,
path segment or header, because those land in request logs and an employee's
email is PII. `sub` is your internal user id, so it is used as an email only
when it actually looks like one; otherwise the answer is `unknown_user`.

### Response — always `200`

```json
{"ready": true, "online": true, "exempt": false, "reason": "ok", "last_seen_seconds": 12}
```

| Field | Meaning |
|---|---|
| `online` | the panel's heartbeat is fresher than 90s |
| `ready` | `online` **and** an operator role **and** active **and** chat-enabled (no button involved) |
| `exempt` | this account is not an operator — never block it |
| `reason` | why (see below) |
| `last_seen_seconds` | age of the last heartbeat, `null` if it never beat |

### `reason` values and what to show the agent

| `reason` | Block? | Message for the agent |
|---|---|---|
| `ok` | no | — |
| `offline` | **yes** | "Open the chat panel to start working" |
| `inactive` | **yes** | "This account is deactivated — contact your manager" |
| `wrong_role` | never | — (owner/admin/dev/supervisor and any future role) |
| `chat_disabled` | never | — (account removed from the shared chat system by management) |
| `unknown_user` | never | — (we do not know this email; not our call to make) |

Presence is the heartbeat pulse; agents no longer need to press anything.
`exempt=true` means: do not block them and do not ask them to enter chat,
whatever the reason. (`not_ready` no longer exists as a reason — spec v2.4
A1 removed `is_ready` from this gate.)

`inactive` exists because deactivating an account does not reach a live
session: the panel keeps heartbeating on a JWT that has not expired yet, so a
disabled employee can look perfectly present. Login already refuses them; this
gate must not answer the opposite about the same person.

### The two rules the CRM MUST implement

1. **Block only when `exempt === false` AND `ready === false` AND the answer
   arrived within 2 seconds.**
2. **Any timeout, network error, `401`, `429` or `503` → do NOT block (fail
   open) and log it.** A gate that blocks when it is broken costs more than one
   it fails open on.

### Rate limit and visibility

The endpoint carries its own budget — **600 checks per minute per caller IP** —
because the shared per-IP limit is sized for browser traffic and your backend
is a single IP. Exceeding it returns `429`, which rule 2 turns into "do not
block".

`GET /health` reports `presence_gate: {checks, blocked, errors}` — aggregate
only. `errors` counts every `401`/`503` we answered, because those are exactly
the responses that switch the gate **off** while it still looks alive from the
outside. There is deliberately **no per-agent record** of who was blocked and
when: that is employee surveillance, and if it is wanted it belongs on the CRM
side, where the manager who asked for it can be held to it.

### Why 90 seconds

The panel heartbeats every 5s, so the window tolerates 18 missed beats — a
network blink never blocks anyone. The window exists because **`is_ready` is
never cleared automatically**: it is set when the agent presses the button and
stays set, so an agent who pressed Ready on Monday and went home would still
read as ready today. Only a fresh pulse makes the flag mean anything.
