# "On the site now": the contract between the API, bbc-live, the widget and the panel

An agent sees the visitors on the site right now whom Alex's card didn't start a chat with (anonymous: a
page, a trip from the page, where they came from, what came of the card), holds one to say hello, and the
visitor's widget shows the hello as Alex's card shows (the owner, 23 Nov 2026): in the chat when it is
open, in the place of Alex's opening; else beside the chat button on a phone; else, on a computer, the
chat opens with it. The visitor's answer opens a chat that is already the agent's.

Who does what:

| Piece | Language | Does | Never |
|---|---|---|---|
| `bbc-live` | Go | the widget's beats, the live list, the claims, the waiting poll that delivers a hello | read or write the database; issue a token |
| the API | Python | issues every token; checks and sends the hello; creates the chat at the tap | hold a widget's poll open |
| the widget | TS | beats after 20 s on the page while there is no chat (the chat closed, or open on an opening nobody answered), with what came of Alex's card; waits for a hello; shows it as plain text; answers with its tap; from the page's start, sends the visit registry its snapshots | send anything the visitor typed |
| the panel | TS | lists who Alex's card didn't start, claims, writes the hello | talk to Redis |

Redis holds coordination only. Everything here can disappear: the visitor is simply not listed for a
minute, a hello not delivered expires. No key or value carries a name, an e-mail or a phone. The visit
registry's visits (below) carry the visitor's address and browser line until the API stores them, three
days at most.

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
| `POST /v1/beat` | widget, `X-Live-Pass` | 200 counted; 202 a beat under 5 s after the last, not counted; 400 not JSON; 401; 503 not configured or Redis down. The widget's last beat, `has_chat`, goes once more after the gap when it didn't count (202, 5xx, or lost on the way) |
| `GET /v1/inbox` | widget, `X-Live-Pass` | 200 `{"messages":[…]}` at once or the moment a hello lands; 204 after 25 s with nothing; 429 a fourth tab waiting |
| `POST /v1/dismiss` | widget, `X-Live-Pass` | 200: "not now", nobody says hello for an hour, the claim and the inbox go |
| `POST /v1/visit` | widget, `X-Live-Pass` or `"pass"` in the body | 200 kept; 400 not JSON or no `page_id`; 401; 429 over the visitor's pace (20 at once, then one per 3 s); 503 Redis down. The visit registry, below |
| `GET /v1/live` | panel, `Bearer l1…`, roles sales, owner, admin, dev, supervisor | 200 `{"data":[visitor…],"max_claims":3,"claim_seconds":600}`; a reader who may hold a visitor is noted in `live:greeters` (the agent-first test) |
| `POST /v1/claim` `{"visitor_id"}` | panel, role sales, line sales or all | 200 `claimed`/`mine`; 409 `taken`, `full`, `declined`, `in_a_chat`; 410 `gone` |
| `POST /v1/release` `{"visitor_id"}` | panel | 200 `{"released": bool}` |
| `GET /health` | anyone | Redis, the secrets present, counters |

A beat (all optional; anything else is dropped): `page` (a path: the query and the fragment are cut,
they can carry an e-mail), `from`, `to` (`[A-Z]{3}`), `depart`, `return` (`YYYY-MM-DD`), `cabin`
(economy, premium, business, first), `pax` (1–9), `source` (kayak, ads, site), `device` (phone,
computer), `site` (bbc, bct), `chat_open`, `has_chat`, `page_type` (the site's word for the page:
home, route, destination, origin, airline, deals, search, flight, quote, thank_you, blog, about, legal,
other), `activity` (the visitor's last steps on the site, oldest first, joined by `;`). Nothing the
visitor typed is in it. `alex_at` (the owner's agent-first test, below): when Alex's card may come, unix seconds
by the API's clock, kept only as a whole number within 900 s of bbc-live's now. `alex_card`: what came of Alex's
card on this visit, `shown` (no answer yet), `dismissed` ("Not now" or its ✕) or `engaged` (the visitor answered
it: a reply, a channel, a message); Alex's opening in a chat the visitor opened themselves counts as his card
(moment `note`). `alex_card_s`: the seconds since it showed, by the visitor's clock, a whole number from 0 to
86 400; bbc-live keeps the time it showed by its own clock (the visitor's may be off). `alex_trigger`: the moment
that showed it (kayak, ads, quote, reader, exit, note). A state it doesn't know or seconds out of range leave
all three out; a moment it doesn't know leaves only the moment out. `tz`: the browser's time zone, an IANA name
(`UTC`, `Australia/Sydney`, `America/Argentina/Buenos_Aires`; another shape is left out). `returning`: true when
this browser was on the site before this visit (the widget's `visitCount.ts`: a visit is a run of pages and beats
with no 30-minute gap, counted in localStorage from the first page the widget saw); left out otherwise. `has_chat` is true on the widget's last
beat, the one it sends when the visitor's chat starts (once more after the 5 s gap if it didn't count: 202, 5xx,
or lost on the way): the panel stops listing them at once. Once true, `has_chat` stays true for the visit
(`beat.lua`): a beat sent before it that lands after it never takes it back.

A step is one of: `search[:FROM-TO][:YYYY-MM-DD]` (a code is `[A-Z]{3}` or `?`), `quote_opened`,
`quote_step[:N]`, `quote_invalid[:kind|kind…]` (the kinds of the fields the form flagged: name, email,
phone, origin, destination, dates, passengers, cabin, trip_type, message, consent, captcha, other),
`quote_submitting`, `quote_failed:server|network|captcha|other`, `quote_submitted`,
`contact[:phone|whatsapp|email]` (the kind of link clicked, never the number or the address). The widget
builds them from the site's events (`siteSignals.ts`, `activityLine`) and sends a line of 300 characters
at most; bbc-live drops a line over 600, keeps the steps that match one of these shapes exactly and the
last 12 of them.

A listed visitor: `visitor_id`, `first_at`, `last_at`, `seconds_on_site`, `page`, `page_type`, `trip`,
`activity` (a list of steps, oldest first), `source`, `device`, `site`, `chat_open`, `has_chat`,
`claimed_by` (an agent id), `dismissed`, `alex_at` (left out when there is none), `alex_card`, `alex_card_s`
(seconds since it showed, by bbc-live's clock at the list), `alex_trigger` (the three left out when Alex's card
didn't show), `tz` and `returning` (left out when the beat had none). bbc-live lists everyone who beats; the panel shows the visitors Alex's card didn't start a chat
with: `alex_card` `dismissed`, or `shown` with `alex_card_s` 30 or more (70 % of the answers his card gets come
within 30 s), and no `has_chat`; plus the ones the agent holds, whatever became of them, and while the
agent-first test runs the ones Alex waits on. The freshest card first. The row says what came of the card in
words, shows the last four steps in words and starts the hello from the last one: a quote form that stuck or
failed is offered a hand, a sent one a thank-you. A visitor who wrote is in a chat already, not in this list.

The groups (the owner, 25 Nov 2026; the panel's `features/live/view.ts` `groupOf`): where the group matters, the
row names it and has its hello ready, sent in one tap (the claim, then the hello). The first that fits:
`form` (stuck in the quote form: it flagged a field, didn't go through, or was opened and not sent),
`anz` (Australia or New Zealand by the time zone, on a computer: 17.9 % answer Skye's card), `soon` (flying in 15
to 45 days by the page's trip, on a computer: 16.5 %), `back` (`returning`: 8.8 %, against 4.8 % for a first
visit). The hello goes out with its group (below), kept with the hello and with the chat its tap starts.

## The API's routes

- The heartbeat (`POST /api/agent/heartbeat`) answers `live: {"url", "token", "expires_at"}` to the
  roles that see the list, while `LIVE_VISITORS` is on and both secrets and `LIVE_SERVICE_URL` are set.
- `POST /chat/live` `{"visitor_id", "live_pass"?}` (the widget, public, never cached), from 20 s on the
  page: `{"enabled": false}`, or `{"enabled": true, "url", "after_s": 20, "beat_s": 15, "hello", "pass",
  "pass_expires_at"}`. The widget keeps the pass and shows it at the next page (`live_pass`): that renews
  it. A refused pass (the id has one and none was shown, too many new ids from the address) answers
  `{"enabled": false}`: the widget stops for the page. Redis failing or cooling down answers
  `{"enabled": false, "later": true}`, and so does a 5xx or 429 from the route: the widget asks again
  after 5 minutes and keeps the config it holds. Both counted in `/health/detail`. The pass is made before
  `pass.lua` binds the id, so the binding outlasts the pass's last renewing instant.
- `POST /api/live/send` `{"visitor_id", "text", "group"?}` (an agent holding that visitor): 1 to 300 characters,
  no e-mail, phone or link in it (`output_guard.foreign_contact_kinds`); runs `send.lua`. `group` (form, anz,
  soon, back: the group the panel named the visitor by) is kept with the hello (`live:hello:<id>`), in the
  audit row and, after a tap, in the chat's `metadata.live_hello.group`; any other value is left out, never
  refused. The visitor's inbox message never carries it.
- `POST /chat/init` with `live_tap`: a valid tap for this visitor takes the hello (`take_hello.lua`),
  and the chat starts as the agent's (assigned, human mode, the hello as its first message); the answer
  carries `live_agent`, `live_hello` and `live_at` (when the hello was stored: the widget that shows it
  already reads messages after it). A tap that doesn't hold (expired, taken, the switch off) is counted and
  the chat starts as any other, without them. Never with `proactive`: a chat that starts with Alex's
  opening takes no tap (the widget sends one or the other). A tap on the hello of the agent's own bot (below) starts
  the chat the same way, marked `metadata.live_hello.bot: true`.

## An agent's own bot (25 Nov 2026)

The owner: an agent connects their own bot, and it works as them through the API (`app/services/live_bot.py`,
`app/api/live_bot.py`), without the three visitors an agent holds at a time.

- The key: the agent takes it in "On the site now" (`POST /api/live/bot-key`, their panel session; `GET` says whether
  one works and until when, `DELETE` revokes it). A JWT signed with the panel's secret, scope `live_bot`, 90 days:
  no panel route takes it as a session (any scope is refused there). It works while it is the agent's newest key
  (`live:botkey:<agent>`), while the agent's `token_version` is the one it was made with (a role change or a
  deactivation ends it), while no "sign out everywhere" came after it, and while the agent may say hello and take
  chats (the agent's row read again at every call, nothing of it kept between calls). Shown once; a new key stops
  the one before. `GET` checks it the same way: a key a sign-out or a role change stopped is no longer active.
- The bot's calls, `Authorization: Bearer <key>`, the admin envelope with the refusal's `code`:
  - `GET /api/live/bot/visitors`: the visitors the panel lists for the agent (Skye's card unanswered 30 s or "Not
    now" to it, the agent-first test's wait, the ones the agent or their bot holds), each with `state` (free, mine,
    taken, my_bot, bot, declined, in_chat), `may_greet` and `why_not`, and the hello the panel has ready in the
    agent's name (`ready_hello`, `group`).
  - `POST /api/live/bot/hello` `{"visitor_id", "text"?, "group"?}`: the hello in the agent's name, `text` or the
    ready one. The API checks the visitor's beat just before (listed for the agent, nobody else holding them, no
    hello lately, no bot's hello today, not in the middle of the quote form: the panel's two minutes); `text`, and
    the ready hello too (it carries the page's destination), is held to 300 characters, no contact, link or price,
    no long dash. Then `bot_send.lua` checks again and sends in one step, without a claim before it: the visitor left
    (-1), in a chat (-2), said "not now" (-3), someone else holds them (-4; the agent themself may, from the panel),
    a hello went already (-5), a bot said hello to them today (-6), a step taken since the API read them (-7,
    `changed`: the quote form opened meanwhile; ARGV[8] is the activity the API judged). The card's state is the
    API's check alone. A 503 may come after the hello went (its answer lost): the list says it, `state` my_bot,
    before the bot tries again. Sent, the hello holds the visitor as
    `bot:<agent>` (`live:claim:<vid>`) for its 15 minutes, in no agent's `live:claims:`: the panel shows "Your bot
    said hello" / "A colleague's bot said hello" with no button, and nobody claims them meanwhile. The widget's
    message is an agent's (`live_inbox_message.json`): the visitor sees the agent's first name.
  - The tap starts the chat as the agent's, as a hello from the panel (assigned, human mode: Skye stays quiet),
    with `metadata.live_hello.bot: true`. A mark that can't be written (tried twice) gives the chat back to Skye:
    its bot would never see it (`/health/detail` `live.mark_failed`).
  - `GET /api/live/bot/chats`: the open chats its own hellos started (`live_hello.agent_id`: not a colleague's bot's
    chat a supervisor moved to the agent) that are still the agent's, in human mode, with `waiting` (the visitor
    wrote last). `GET /api/live/bot/chats/<id>/messages?after=<ISO time>`: what was said, oldest first, `role`
    visitor, agent, ai or system; the chat is checked again after the read. `POST /api/live/bot/chats/<id>/messages`
    `{"text"}`: its answer, through the panel's own send (the agent's name on it, the response clock, the follow-up
    task), held to the same rules as the hello, up to 2,000 characters; the send writes only in a chat still the
    agent's, and never claims one. Any other chat answers 403 `not_the_bots`. A message the
    bot sends counts as the agent being here, as one from the panel does; ten minutes with nothing from either, the
    agent's chats go back to Skye, as any agent's.
- `/health/detail` counts it all under `live_bot`.

## Where the hello shows (the widget, 23 Nov 2026)

- The chat open with no conversation (Alex's opening unanswered, any screen): the hello takes the opening's
  place, the agent's first name above it. The visitor's first message goes to `/chat/init` with `live_tap`
  and no `proactive`; a tap that didn't hold takes the hello off the screen (the chat never had it).
- A phone with the chat closed: the card beside the chat button (Alex's unanswered card makes way for it);
  "Reply" makes the chat with the tap at once and opens it.
- A computer with the chat closed: the chat opens with the hello, as Alex's opening does.
- ✕, Escape or "Not now" on it: `POST /v1/dismiss`, except while a first message carries its tap (that is an
  answer). The hello is kept for the visit's next pages (sessionStorage, with bbc-live's place) until it runs
  out, is answered or dismissed, and can be dismissed before the page's config comes; a chat kept open on one
  that ran out since stays closed on the next page.

## The owner's agent-first test (22 Nov 2026)

Does a person who writes first get more answers than Alex's card (5.7 % on 15-22 Nov)? The API decides
(`app/services/agent_first_test.py`); bbc-live only carries `alex_at`.

- Two sides, by visitor id (sha256 buckets, experiment `agent_first`), `AGENT_FIRST_TEST_PERCENT` (50, the
  owner's) on the agent's side. The test runs while the hello runs (`LIVE_VISITORS` and `AGENT_FIRST_MESSAGE`
  on); 0 ends it without a deploy.
- Alex's side: `POST /chat/live` answers `{"enabled": false, "test": "alex"}`, so it is never listed and no agent
  greets it. Its cards come as before.
- The agent's side: `POST /chat/live` answers as usual plus `"test": "agent", "hold_s"`. While an agent who may
  say hello has "On the site now" open (`live:greeters`: bbc-live notes the sales line at each read of the list,
  every 5 s while the page is open and visible; the API counts a read in the last 30 s; the heartbeat, on every
  page of the panel and in a hidden tab, is no sign of it), `POST /chat/proactive/offer` answers
  `{"show": false, "arm": "agent_first", "reason": "waiting", "retry_after_s", "alex_at"}` for
  `AGENT_FIRST_HOLD_S` (120, the owner's) from the visitor's first moment (`live:hold:<vid>`): the widget gives the
  moment back, asks again after the wait and beats `alex_at`, and the panel shows the countdown. A hello in that
  time (`live:said`) or a "Not now" (`live:dismissed`) answers `"reason": "greeted"` / `"declined"`: no card that
  visit. Nobody said hello: the card as usual, with `"after_hold": true`. The tapped note is never held.
- Every answer names the side (`"test"`) while the test runs; the widget puts it on its PostHog events
  (`agent_test`), the API on the chat's metadata (`agent_test`), and the heartbeat's live block carries
  `"test": {"percent", "hold_s"}`. `/health/detail` counts it under `agent_first_test`. Redis failing holds nobody.

## Redis keys

| Key | Type | Value | Life | Written by |
|---|---|---|---|---|
| `live:visitors` | sorted set | visitor id, score = unix seconds of the last beat | trimmed at each beat | bbc-live |
| `live:v:<vid>` | hash | the beat's fields, `first_at`, `last_at` | 60 s after the last beat | bbc-live |
| `live:claim:<vid>` | string | the agent id holding the visitor; `bot:<agent>` while that agent's bot's hello is out | 600 s; a bot's 900 s | bbc-live (claim), the API (`bot_send.lua`), the API and bbc-live (release) |
| `live:claims:<agent>` | set | the visitors the agent holds | 600 s | bbc-live |
| `live:dismissed:<vid>` | string | `1` | 3600 s | bbc-live |
| `live:inbox:<vid>` | list | messages for the widget (JSON, `live_inbox_message.json`) | 900 s | the API |
| `live:hello:<hello id>` | string | the hello for `/chat/init` (JSON) | 900 s | the API |
| `live:said:<vid>` | string | the agent who said hello (`bot:<agent>` for an agent's bot) | 900 s | the API |
| `live:bot:<vid>` | string | `1`: an agent's bot said hello to this visitor | 86400 s | the API (`bot_send.lua`) |
| `live:botkey:<agent>` | string | the agent's newest bot key: `{"id", "iat", "tv"}` (JSON; never the key itself) | 90 days | the API |
| `live:passed:<vid>` | string | `1`: this id has a pass; the next needs it | 25 h after the last pass: its 24 h and the hour it still renews | the API (`pass.lua`) |
| `live:hold:<vid>` | string | unix seconds when Alex's wait began (the agent-first test) | 900 s | the API |
| `live:greeters` | sorted set | user id, score = unix seconds of the last read of the list by an agent who may hold a visitor (the agent-first test) | a day after the last read, trimmed at each read | bbc-live |
| `rl:livepass:<address>:<hour>` | string | new ids that address got passes for this hour; `<address>` a keyed hash, never the address | 1 h | the API (`pass.lua`) |
| `live:reg` | sorted set | visitor id of an open visit, score = unix seconds of its last snapshot | until the visit ends | bbc-live (`visit.lua`, `close.lua`) |
| `live:reg:<vid>` | hash | the open visit (the registry, below) | 6 h after the last snapshot; ended at 30 min | bbc-live |
| `live:ended` | list | keys of ended visits, oldest first | until the API stores them | bbc-live pushes, the API removes (`LREM`) |
| `live:ended:<vid>:<first_at>` | hash | an ended visit, as it was; `link_tries`: the runs its chat lookup got no answer | 3 days; the API deletes it once stored | bbc-live renames, the API counts `link_tries` (only while the hash is there: one script) and deletes |
| channel `live:inbox` | pub/sub | a visitor id: wake their poll | — | the API publishes, bbc-live listens |

The scripts are in `contracts/live/` and copied next to the code that runs them (bbc-live
`internal/live/lua/`, the API `app/services/live_lua/`); each suite fails when its copy differs.
`list.lua`, `claim.lua` and `dismiss.lua` build keys from ids inside the script: one Redis, not a
cluster.

## The visit registry (25 Nov 2026)

The owner: every visit (about 2,000 a day) kept, with the visitor's address and the contacts we catch, no time
limit for now, to judge the card's words per group of visitors on everything that happened. Switch:
`VISIT_REGISTRY` (`app_settings.flags`, off at merge). Migration 056 (`site_visits` and its two views) runs
before it is turned on.

- **The pass.** While the registry is on, `POST /chat/live` answers every visitor with the pass and bbc-live's
  place and `"visits": true`, plus `"country"` (the two letters Cloudflare saw, when it saw one): also with the
  live list off (`"enabled": false`) and on the agent-first test's Alex side (`"enabled": false, "test": "alex"`),
  which stay unlisted and unbeating. The widget asks at the page's start; the live list takes the same answer 20 s
  in (one ask a page).
- **The snapshot.** `POST /v1/visit`, from the page's start: at once, 1.5 s after anything the widget does (the
  events it captures for PostHog, as short words), every minute while the page is in sight, and when it is hidden
  or left (`sendBeacon`). Plain text JSON, the pass inside (`"pass"`), so no preflight. Fields, all optional but
  `page_id`: `page_id` (`[A-Za-z0-9_-]{6,32}`, one per page load), `seq` (the snapshot's number on its page: whole,
  1 to 10 000; anything else counts as none), `page`, `page_type`, `page_s` and `seen_s`
  (seconds since the page loaded, and in sight: whole, 0 to 86 400), the trip as in a beat, `source`, `channel`
  (kayak, google, meta, bing, ads, search, referral, direct, other), `utm_source`, `utm_medium`, `utm_campaign`
  (letters, digits, spaces and `_ . , : | + / ( ) -`, 80 at most; anything else drops the tag), `referrer` (a host),
  `device`, `site`, `lang`, `tz`, `country` (`[A-Z]{2}`), `ph_session` (a UUID), `events` (the page's widget events,
  `;`-joined: `word[:qualifier[:qualifier]][*count]`, words and qualifiers from fixed lists, 40 at most),
  `steps` (the site's steps on the page, as in a beat, 30 at most), `card` (`trigger`, `variant`, `surface`,
  `state` shown/dismissed/engaged, `s` seconds since it showed, `answer` and `answer_s` once answered), `has_chat`,
  `conversation_id` (a UUID). bbc-live adds the address (the last `X-Forwarded-For` entry: Railway's edge adds it)
  and the browser line (`User-Agent`, printable, 300 characters at most). Never a name, an e-mail, a phone or
  anything typed.
- **The visit** (`visit.lua`): one per visitor while snapshots keep coming. The first snapshot with a value sets
  where they came from, the screen, the language, the time zone, the country, the address, the browser, the
  PostHog session, the chat's id and the card's moment, words and place (`hsetnx`); the latest non-empty value sets
  the last page and the trip; `has_chat` once `1` stays; the card's state only moves forward (shown, dismissed,
  engaged), and its answer and answer time go with the state that won: an engagement after "not now" takes its own,
  a "not now" that arrives after it changes nothing. Its times are bbc-live's clock. Each page is `pg:<page id>`
  (JSON: path, kind, seconds, seen, events, steps, when last heard, `n` the snapshot's number), in first-heard
  order in `pages`, replaced only by a newer snapshot of it (a fetch and the page's last beacon can cross): a higher
  or equal `n` when both carry one; without it, more seconds since the page loaded, and at the same second no fewer
  events and steps (a count grows as one item: `chat_msg*2` to `chat_msg*3`, which `n` orders); past 40 pages they
  are counted (`pages_over`), not kept.
- **The end.** A visit with no snapshot for 30 minutes has ended: `close.lua` (every 30 s) renames it to
  `live:ended:<vid>:<first_at>` and pushes that key on `live:ended`; a snapshot that finds its visitor's visit gone
  quiet ends it the same way first. Lost (`/health/detail`): an open visit that ran out before it ended.
- **The API** (`app/services/visit_registry.py`, every minute) reads up to 200 queued keys, stores one row a visit
  (`site_visits`, UNIQUE visitor id and start: a visit taken twice is one row), links the chat by the visitor's id
  when the widget didn't name it (the first chat the visitor began from 2 minutes before the visit's first snapshot
  to 30 minutes after its last, one bounded read a visit), then removes from the queue exactly the keys it stored
  (`LREM`, never a range: two schedulers run during a deploy). A table not there yet, or a failed write, leaves the
  queue for the next run. A visit whose chat lookup got no answer stays queued for the next run, 5 runs at most
  (`link_tries` in its hash), then goes in without its chat (`/health` `link_given_up`).
  Contacts are never copied: `site_visits_full` reads them from the chat and the lead; `site_visits_daily` counts
  per New York day, group (source, channel, screen, came back) and card (moment, words).

## Numbers

A visitor beats every 15 s while the page is visible, from 20 s on the page; is listed while a beat is
under 45 s old, from 30 s after Alex's card went unanswered (at once after "Not now"). An agent holds at most 3 visitors, for 10 minutes each; one hello a visitor per 15
minutes, whoever sends it. A "not now" keeps everyone away for an hour.
