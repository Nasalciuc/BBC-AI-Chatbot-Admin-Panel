# Chatbot SSO — CRM ↔ chat.buybusinessclass.com

Auto-login for the embedded chatbot: if a user is logged into the CRM, they are
authenticated in the chatbot automatically, with no second login.

This document is the integration spec for the **chatbot backend** developer.

---

## 1. How the authenticated user arrives

When a user opens `/chat` in the CRM, the page loads the chatbot in an iframe
with a signed token on the URL:

```
https://chat.buybusinessclass.com/?token=<JWT>
```

The chatbot backend must read `token` from the **query string** on page load.

---

## 2. JWT contract (what the CRM sends)

|                   |                                                               |
| ----------------- | ------------------------------------------------------------- |
| **Algorithm**     | `HS256`                                                       |
| **Shared secret** | `CHAT_SSO_SECRET` (delivered separately, securely — see §4)   |
| **Transport**     | query param `?token=`                                         |
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
  `?token=` from the URL, POSTs it to the backend, applies the returned BBC
  session on success, then strips `?token=` from the URL.
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
