# Facebook Messenger: bbc-messenger and the API (25 Nov 2026)

Skye answers on Messenger for the BuyBusinessClass and Business Class Tickets Pages, as on the site; agents answer
from the panel. One Meta app, the two Pages. Switch: `MESSENGER_CHANNEL` (`app_settings.flags`, off at merge), with
the API's `MESSENGER_GATEWAY_URL` and `MESSENGER_GATEWAY_SECRET` set. No migration.

| Part | Language | Does | Never |
|---|---|---|---|
| bbc-messenger | Go, stdlib | checks Meta's signature; keeps each person's events in order; hands each to the API; sends what the API answers; sends the API's other messages (`/v1/send`) | keeps a chat, reads a profile, logs what a person wrote or their PSID |
| the API | Python | keeps the chat; answers each message with the widget's own turn; sends an agent's message through the gateway | reads Meta's profile name (a name comes only from what the person writes) |

## Meta → the gateway

- `GET /webhook`: Meta's check when the webhook is saved. `hub.mode=subscribe` and `hub.verify_token` equal to
  `META_VERIFY_TOKEN` (constant-time): 200 with `hub.challenge` as plain text; anything else 403.
- `POST /webhook`: while a variable is missing (`/health` `ready: false`), 503 before anything is read
  (`not_ready`): Meta delivers it again once the gateway is set up. `X-Hub-Signature-256: sha256=<hex
  HMAC-SHA256(META_APP_SECRET, raw body)>`, else 403. Body over 1 MiB: 413. Signed but not `"object": "page"`: 200,
  counted (`unreadable`). Each event, oldest first (Meta does not promise the order in a batch): a Page without a
  token is skipped (`unknown_page`); the app's own echoes are left out (`own_echoes`); read, delivery and reaction
  events are skipped; an event already taken (by Meta's message id, 2 hours, at most 200,000 kept, the oldest
  forgotten first; this process's memory) is skipped (`duplicates`); the rest go to the person's worker (the Page
  and PSID pick it: one person's events in order). The check, the queueing and the mark are one step: the same
  delivery arriving twice at once is taken once. A full queue answers 503 and Meta delivers the batch again
  (`queue_full`); an event is marked taken only once queued, so the part already queued comes back as duplicates
  and the rest is taken. Each person with an event in the part not taken then waits for their first one: a later
  delivery of theirs is refused (503, `held`) until that event is taken, so it never overtakes it, whenever Meta's
  deliveries come; after 15 minutes without it the hold goes (`hold_expired`). Stopping (a deploy): 503
  (`closing`). 200 at once otherwise: Meta wants it within 5 seconds, after 15 minutes of failures it warns, after
  an hour it unsubscribes, so the gateway never waits for the API before answering Meta.

Webhook fields to subscribe on each Page: `messages`, `messaging_postbacks`, `messaging_referrals`,
`message_echoes`, `messaging_handovers`.

## The gateway → the API

`POST {API_URL}/api/channels/messenger/inbound`, one event a call, signed:

```
X-Gateway-Time: <unix seconds>
X-Gateway-Signature: sha256=<hex HMAC-SHA256(MESSENGER_GATEWAY_SECRET, "<unix seconds>." + raw body)>
```

The API refuses a signature that does not hold or a time more than 5 minutes from its own (401). The body:

| Field | |
|---|---|
| `kind` | `message`, `postback` (a button of the Page: Get Started, the menu), `referral` (opened the thread from an m.me link or an ad, nothing written yet), `echo` (someone wrote from the Page's inbox) |
| `page_id`, `psid`, `site` | the Page, the person on it, the Page's brand (`bbc`, `bct`) |
| `mid`, `timestamp` | Meta's message id (what makes a delivery made again the same event), milliseconds |
| `text`, `quick_reply` | what they wrote; the payload of a quick reply they tapped (a button's kind, or their own number or e-mail from their profile) |
| `postback`, `postback_title` | a Page button's payload and its words |
| `referral` | `ref`, `source` (`ADS`, `SHORTLINK`, …), `type`, `ad_id` |
| `attachments` | the kinds of what they sent (`image`, `audio`, `video`, `file`, `location`, …), never the file |
| `standby` | heard while another app owns the thread: kept, never answered |

Answers: 200 `{"replies": [{"text", "quick_replies"?}, …]}`, the replies to send in order (the quick replies of the
batch go on its last message). 409: the same event is still being answered. 422: not an event. 503 with
`X-Messenger-Kept: 1`: the event failed, and the API keeps it and answers it later itself (below). 503 with
`X-Messenger-Off: 1`: the channel is off, nothing is kept. Any other 503: the event failed and is kept nowhere.

The gateway keeps an event until the API has it. A network failure, a 5xx without `X-Messenger-Kept` (the API's
proxy while it restarts), 429 or 409: tried again after 2, 5 and 15 seconds, then every minute until an hour has
passed since the first try (`forward_failed` after that; the events behind it in its worker's queue wait, in
order). Kept by
the API: tried after 2, 5 and 15 seconds only, then left to it (`forward_kept`). The channel off: final
(`api_off`); another 4xx: final (`forward_refused`). While the API thinks, the person sees the message read and Skye
typing (`mark_seen`, `typing_on`).

What the API does with an event:

- **The chat.** Visitor id `msgr-` + 40 hex of SHA-256(page id + ":" + PSID). The person's newest chat written to in
  the last 7 days (its last change, or their last message) goes on, closed or not (the turn reopens it, or answers
  after the sale, as the widget's chat); else the chat is found as the widget's is (an open one of theirs), or a
  new one starts: tunnel sales, metadata `site`, `chat_channel: "messenger"`, `messenger: {page_id, psid}`,
  `utm_source: "messenger"`, `utm_medium` (`messenger_ads` for an ad, else `messenger`), `utm_campaign` (the ref),
  `messenger_referral`. A referral before the first message waits in Redis for the chat.
- **The turn.** A message (or a Page button, as its words) takes the widget's own turn (`app.api.chat.chat`): the
  blocklist, the message limit, the chat's doors, an agent who holds the chat, the card's buttons (a quick reply whose
  payload is a button's kind is that button), the cache, Skye. What the turn stores for the person (Skye's answer,
  a system line) goes back as the replies, as stored (through the output guard). Its answer too when it stored none
  (told by what was stored, never by comparing texts), guarded on the way, except "Your message has been sent to
  the specialist" after each message to an agent ("connecting you with a specialist" goes once in 30 minutes). A
  turn the chat refuses (a 4xx: the blocklist, a limit) is kept as the chat kept it, with no reply; one it could not
  take now (a 5xx: the message not saved) fails the event.
- **Buttons.** The answer's buttons become text quick replies (the label, at most 20 characters; the kind as the
  payload); its suggestions too (payload `say`: tapped, they are what the person typed). Right after the person chose
  SMS or e-mail, the reply offers their own number (`user_phone_number`) or address (`user_email`) in one tap; Meta
  shows it only when their profile has one, and they can always type it.
- **No text.** A photo, a voice message or a file alone is kept as `[photo]`, `[voice message]`, … first, then Skye
  asks for words.
- **Kept, not answered.** An echo from the Page's inbox is kept as the agent's message (never sent back), and Skye
  leaves the thread to the inbox for 30 minutes: the person's messages meanwhile are kept, not answered. So is a
  message heard in standby. The 30 minutes not saved: the echo's event fails (and is delivered again).
- **Saved once.** What an event stores itself (the person's words kept without a reply, a photo's placeholder and
  Skye's ask for words, an echo) is saved before anything answers it, and never twice: a row an earlier delivery
  saved is marked by the event's key (`msgr:saved:<key>:<part>`, a day), and one whose save had no answer is looked
  for among the chat's last rows (10 minutes) before it is saved again (`saved_once`). Not saved: the event fails.
- **Delivered again.** Redis keeps each event's mark (`msgr:event:<mid>`: `pending` for 2 minutes, then the replies
  given and who sent them, a day): a delivery made again gets the same replies and no second turn (none when the
  API sent them itself); one made while the first is still answered gets 409. An event that fails clears its mark,
  so the next delivery is answered. Redis down: answered without the mark (`no_dedupe`).
- **Answered later.** From the moment it is taken until it is answered, an event is in the inbox (`msgr:inbox`, its
  JSON beside it, 2 days). One whose turn failed is answered by the scheduler's minute (`messenger_recover`) once
  10 minutes have passed (longer than the gateway's own tries of a kept event), its replies sent through
  `/v1/send`; the gateway down then, the same replies go the next minute, without a second turn. One process at a
  time (a lock in Redis); after 60 tries, or unreadable, the event is given up (`recover_given_up`, logged
  without its words). Redis down: not kept (`not_kept`), the gateway's tries only.

## The API → the gateway

`POST {MESSENGER_GATEWAY_URL}/v1/send`, signed as above, for what is stored for a Messenger chat outside a turn: an
agent's message from the panel, the "joined" line, anything else.

```json
{"page_id": "…", "psid": "…", "messages": [{"text": "…", "quick_replies": […]}], "tag": "HUMAN_AGENT"}
```

`tag` only once Meta approved Human Agent (the owner's decision); no other tag is taken. Unknown fields: 400. 200
`{"sent": n, "parts": m, "parts_sent": k}`: n messages went out whole; their texts made m Messenger messages (a
long one is several), k of which went out, in order. With `"failed": {"kind", "code", "subcode"}` when one did not:
`window_closed` (the person last wrote over 24 hours ago), `unavailable` (blocked the Page, gone), `thread_owned`
(the Page's inbox owns the thread), `token` (the Page's token is wrong or expired), `rate`, `temporary`, `unknown`
(Meta had the whole call and its answer never came: maybe with the person, never sent again), `timeout` (the send's
60 seconds ran out before the next call could start: nothing in flight), `other`. The 60 seconds count the wait
for the person's other sends too, so the answer always comes back (the HTTP server's own limit is 75). 401 a call
not signed by the API, 404 a Page the gateway has no token for.

The API sends a chat's rows one after another (an agent's "joined" line and their message never cross); a row
waiting for the chat's previous one is not sent if the channel was turned off meanwhile (`off_skipped`). Where a
chat answers to is in Redis (`msgr:conv:<conversation id>`, 30 days, renewed by each event); an agent's message to a
chat Redis no longer knows reads the chat's metadata. When an agent's message did not go out, the agent sees a line
in the chat, stored and never sent: past the 24 hours "reach them by SMS or email" (the owner's default until they
decide on Human Agent), unreachable the same, the inbox's thread, "maybe not sent" (`unknown`: send it again if the
client doesn't reply), "only the first k of m pieces" (`partly_sent`: send the rest, not the whole message), or "try
again". A shutdown gives the sends on their way 8 seconds; each agent whose message did not go out then is told (not
sent, or maybe not when it was with the gateway already), and a message saved after it began is told at once
(`cut_at_shutdown`).

## Sending (the gateway)

`POST https://graph.facebook.com/v26.0/me/messages`, the Page's token in `Authorization: Bearer` (never in the
address), `messaging_type: RESPONSE` (`MESSAGE_TAG` with a tag). A text over 2,000 characters goes as several
messages, cut at a paragraph, a line, a sentence or a space. The quick replies go on the batch's last message
(Messenger shows them on the newest one only): those of the last reply that has any. At most 13 quick replies,
titles of 20 characters. One person's sends one after another. Rate limits (a Graph code, or HTTP 429 alone) and
Meta's own trouble are tried again (2, 5, 15 seconds), and so is a network failure before the call went out. A call
Meta had whole and never answered is never tried again (the Send API takes no key that would make a second send the
same message): `unknown` (`unconfirmed`). A 200 is a message sent, whatever its body. A call starts only while 10
seconds (the Meta client's own limit) are left of the send's time. The rest is counted by kind.

## Redis (the API's)

| Key | Type | Holds | Lives |
|---|---|---|---|
| `msgr:event:<mid or kind:page:psid:time>` | string | `pending`, then `{"replies", "by": "gateway" \| "api"}` | 2 min, then a day |
| `msgr:inbox` | sorted set | the events taken and not answered yet, by when they were taken | until answered |
| `msgr:inbox:<key>` | string | one of them, as the gateway handed it over | 2 days |
| `msgr:inbox:tries:<key>` | string | the recovery's tries of it | 2 days |
| `msgr:saved:<key>:<part>` | string | `trying`, then `saved`: a row the event stored | a day |
| `msgr:recover:lock` | string | one process recovers at a time | 5 min |
| `msgr:conv:<conversation id>` | string | `{"page_id", "psid"}` | 30 days, renewed by each event |
| `msgr:ref:<visitor id>` | string | a referral before the first message | 7 days |
| `msgr:queued:<conversation id>` | string | "connecting you with a specialist" was said | 30 min |

## Health

The gateway's `GET /health`: `ready` (every variable set, never their values), the Pages it has tokens for, the
queues' depth and capacity, `pending` (events taken and not done with), and the counters: `events`, `forwarded`,
`replies_sent`, `agent_sent`, `bad_signature`, `unreadable`, `too_large`, `unknown_page`, `duplicates`,
`queue_full`, `held`, `hold_expired`, `closing`, `not_ready`, `own_echoes`, `skipped`, `forward_failed`,
`forward_kept`, `forward_refused`, `api_off`, `send_failed` with `window_closed`, `unavailable`, `thread_owned`,
`token_failed`, `rate_limited`, `unconfirmed`, `out_of_time`, `action_failed`, `bad_calls`, `dropped_at_shutdown`
(a deploy that ended with events not done with: queued, or still with their worker).

A deploy (SIGTERM): the webhook takes nothing more (503), and the queued events go to the API while the HTTP server
waits for the requests still running (an agent's send): both share the 25 seconds, so a long send never leaves
the queues without time.

The API's `/health/detail` `messenger`: `enabled`, `events`, `turns`, `replies`, `duplicates`, `still_answering`,
`turns_failed`, `turns_refused`, `kept_unanswered`, `inbox_replies`, `attachments`, `referrals`, `no_dedupe`,
`saved_once`, `not_kept`, `recovered`, `recover_waiting`, `recover_given_up`, `bad_signature`, `sent`, `send_failed`
with `window_closed`, `unavailable`, `thread_owned`, `unconfirmed`, `partly_sent`, `gateway_down`, `no_target`,
`off_skipped`, `cut_at_shutdown`.

## The gateway's environment

`PORT`, `META_APP_ID`, `META_APP_SECRET`, `META_VERIFY_TOKEN`, `META_PAGE_ID_BBC`, `META_PAGE_TOKEN_BBC`,
`META_PAGE_ID_BCT`, `META_PAGE_TOKEN_BCT`, `META_GRAPH_VERSION` (v26.0), `API_URL`, `MESSENGER_GATEWAY_SECRET`. The
secrets live only in the service's variables: never in code, Git, chat, e-mail or Telegram. A token that leaked is
revoked from the System User and replaced.
