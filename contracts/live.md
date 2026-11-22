# "On the site now": the contract between the API, bbc-live, the widget and the panel

An agent sees who is on the site right now (anonymous: a page, a trip from the page, where they came
from), holds one to say hello first, and the visitor's widget shows the hello as a card. A tap on it
opens a chat that is already the agent's.

Who does what:

| Piece | Language | Does | Never |
|---|---|---|---|
| `bbc-live` | Go | the widget's beats, the live list, the claims, the waiting poll that delivers a hello | read or write the database; issue a token |
| the API | Python | issues every token; checks and sends the hello; creates the chat at the tap | hold a widget's poll open |
| the widget | TS | beats after 20 s on the page; waits for a hello; shows it as plain text; taps | send anything the visitor typed |
| the panel | TS | lists, claims, writes the hello | talk to Redis |

Redis holds coordination only. Everything here can disappear: the visitor is simply not listed for a
minute, a hello not delivered expires. No key or value carries a name, an e-mail or a phone.

Switches (`app_settings.flags`, the API): `LIVE_VISITORS` (the list, the beats: off at merge) and
`AGENT_FIRST_MESSAGE` (claims, hellos, taps: off at merge). Off, the API hands out no live token and
tells the widget nothing; bbc-live then has nobody to serve.

## Tokens

The API issues them all, with one secret (`LIVE_TOKEN_SECRET`); bbc-live only reads. The prefix keeps
one kind from passing for another. `contracts/live_tokens.json` holds vectors the Python suite
(`tests/test_live_visitors.py`) and the Go suite (`internal/token`) both check.

| Token | Shape | Signed with | Life | Who shows it to whom |
|---|---|---|---|---|
| pass | `b1.<payload>.<hex hmac>`, payload `{"exp","iat","v"}` | `LIVE_TOKEN_SECRET` | 24 h | the widget (from `POST /chat/live`), to bbc-live (`X-Live-Pass`) |
| agent | `l1.<payload>.<hex hmac>`, payload `{"exp","iat","r","t","u"}` | `LIVE_TOKEN_SECRET` | 60 s | the panel (from the heartbeat), to bbc-live (`Authorization: Bearer`) |
| tap | `t1.<payload>.<hex hmac>`, payload `{"a","exp","h","iat","v"}` | `LIVE_TOKEN_SECRET` | 15 min | the widget (inside a hello), to the API's `/chat/init` |

- `<payload>` is base64url without padding of the JSON with sorted keys and no spaces; the HMAC is
  SHA-256 over `<prefix>.<payload>`, hex.
- The pass is not the chat's visitor token (`v1`, `VISITOR_TOKEN_SECRET`): a visitor who never opened the
  chat has no `v1`, and "On the site now" is for exactly those. bbc-live reads no `v1`.
- A visitor id gets its first pass without proof, its next ones only by showing the last (`pass.lua`,
  run by the API): an id an agent sees in the list is not theirs to beat, poll or dismiss with. New ids
  are limited to 20 an hour per address.
- `u` user id, `r` role, `t` tunnel scope; `v` visitor id, `a` agent id, `h` hello id.

## bbc-live's routes

| Route | Who | Answers |
|---|---|---|
| `POST /v1/beat` | widget, `X-Live-Pass` | 200 counted; 202 a beat under 5 s after the last, not counted; 400 not JSON; 401; 503 not configured or Redis down |
| `GET /v1/inbox` | widget, `X-Live-Pass` | 200 `{"messages":[…]}` at once or the moment a hello lands; 204 after 25 s with nothing; 429 a fourth tab waiting |
| `POST /v1/dismiss` | widget, `X-Live-Pass` | 200: "not now", nobody says hello for an hour, the claim and the inbox go |
| `GET /v1/live` | panel, `Bearer l1…`, roles sales, owner, admin, dev, supervisor | 200 `{"data":[visitor…],"max_claims":3,"claim_seconds":600}` |
| `POST /v1/claim` `{"visitor_id"}` | panel, role sales, line sales or all | 200 `claimed`/`mine`; 409 `taken`, `full`, `declined`, `in_a_chat`; 410 `gone` |
| `POST /v1/release` `{"visitor_id"}` | panel | 200 `{"released": bool}` |
| `GET /health` | anyone | Redis, the secrets present, counters |

A beat (all optional; anything else is dropped): `page` (a path: the query and the fragment are cut,
they can carry an e-mail), `from`, `to` (`[A-Z]{3}`), `depart`, `return` (`YYYY-MM-DD`), `cabin`
(economy, premium, business, first), `pax` (1–9), `source` (kayak, ads, site), `device` (phone,
computer), `site` (bbc, bct), `chat_open`, `has_chat`. Nothing the visitor typed is in it.

A listed visitor: `visitor_id`, `first_at`, `last_at`, `seconds_on_site`, `page`, `trip`, `source`,
`device`, `site`, `chat_open`, `has_chat`, `claimed_by` (an agent id), `dismissed`.

## The API's routes

- The heartbeat (`POST /api/agent/heartbeat`) answers `live: {"url", "token", "expires_at"}` to the
  roles that see the list, while `LIVE_VISITORS` is on and both secrets and `LIVE_SERVICE_URL` are set.
- `POST /chat/live` `{"visitor_id", "live_pass"?}` (the widget, public, never cached), from 20 s on the
  page: `{"enabled": false}`, or `{"enabled": true, "url", "after_s": 20, "beat_s": 15, "hello", "pass",
  "pass_expires_at"}`. The widget keeps the pass and shows it at the next page (`live_pass`): that renews
  it. A refused pass (the id has one and none was shown, too many new ids from the address) answers
  `{"enabled": false}`: the widget stops for the page. Redis failing or cooling down answers
  `{"enabled": false, "later": true}`, and so does a 5xx or 429 from the route: the widget asks again
  after 5 minutes and keeps the config it holds. Both counted in `/health`. The pass is made before
  `pass.lua` binds the id, so the binding outlasts the pass's last renewing instant.
- `POST /api/live/send` `{"visitor_id", "text"}` (an agent holding that visitor): 1 to 300 characters,
  no e-mail, phone or link in it (`output_guard.foreign_contact_kinds`); runs `send.lua`.
- `POST /chat/init` with `live_tap`: a valid tap for this visitor takes the hello (`take_hello.lua`),
  and the chat starts as the agent's (assigned, human mode, the hello as its first message). A tap that
  doesn't hold (expired, taken, the switch off) is counted and the chat starts as any other.

## Redis keys

| Key | Type | Value | Life | Written by |
|---|---|---|---|---|
| `live:visitors` | sorted set | visitor id, score = unix seconds of the last beat | trimmed at each beat | bbc-live |
| `live:v:<vid>` | hash | the beat's fields, `first_at`, `last_at` | 60 s after the last beat | bbc-live |
| `live:claim:<vid>` | string | the agent id holding the visitor | 600 s | bbc-live (claim), the API and bbc-live (release) |
| `live:claims:<agent>` | set | the visitors the agent holds | 600 s | bbc-live |
| `live:dismissed:<vid>` | string | `1` | 3600 s | bbc-live |
| `live:inbox:<vid>` | list | messages for the widget (JSON, `live_inbox_message.json`) | 900 s | the API |
| `live:hello:<hello id>` | string | the hello for `/chat/init` (JSON) | 900 s | the API |
| `live:said:<vid>` | string | the agent who said hello | 900 s | the API |
| `live:passed:<vid>` | string | `1`: this id has a pass; the next needs it | 25 h after the last pass: its 24 h and the hour it still renews | the API (`pass.lua`) |
| `rl:livepass:<address>:<hour>` | string | new ids that address got passes for this hour; `<address>` a keyed hash, never the address | 1 h | the API (`pass.lua`) |
| channel `live:inbox` | pub/sub | a visitor id: wake their poll | — | the API publishes, bbc-live listens |

The scripts are in `contracts/live/` and copied next to the code that runs them (bbc-live
`internal/live/lua/`, the API `app/services/live_lua/`); each suite fails when its copy differs.
`list.lua`, `claim.lua` and `dismiss.lua` build keys from ids inside the script: one Redis, not a
cluster.

## Numbers

A visitor beats every 15 s while the page is visible, from 20 s on the page; is listed while a beat is
under 45 s old. An agent holds at most 3 visitors, for 10 minutes each; one hello a visitor per 15
minutes, whoever sends it. A "not now" keeps everyone away for an hour.
