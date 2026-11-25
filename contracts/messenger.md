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
- `POST /webhook`: `X-Hub-Signature-256: sha256=<hex HMAC-SHA256(META_APP_SECRET, raw body)>`, else 403. Body over
  1 MiB: 413. Signed but not `"object": "page"`: 200, counted (`unreadable`). Each event, oldest first (Meta does not
  promise the order in a batch): a Page without a token is skipped (`unknown_page`); the app's own echoes are left
  out (`own_echoes`); read, delivery and reaction events are skipped; an event already taken (by Meta's message id,
  2 hours, this process's memory) is skipped (`duplicates`); the rest go to the person's worker (the Page and PSID
  pick it: one person's events in order). A full queue answers 503 and Meta delivers the batch again; an event is
  marked taken only once queued, so the part already queued comes back as duplicates and the rest is taken. 200 at
  once otherwise: Meta wants it within 5 seconds, after 15 minutes of failures it warns, after an hour it unsubscribes.

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

Answers: 200 `{"replies": [{"text", "quick_replies"?}, …]}`, the replies to send in order (the quick replies ride on
the last). 409: the same event is still being answered (the gateway tries again). 422: not an event. 503: the
channel off, or the event failed (the gateway tries again). The gateway tries again after 2, 5 and 15 seconds on a
network failure, 409, 429 or 5xx, then gives up (`forward_failed`); another 4xx is final (`forward_refused`). While the
API thinks, the person sees the message read and Skye typing (`mark_seen`, `typing_on`).

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
  a system line) goes back as the replies; its answer too when it was not stored, except "Your message has been sent
  to the specialist" after each message to an agent ("connecting you with a specialist" goes once in 30 minutes). A
  turn the chat refuses (the blocklist, a limit) is kept as the chat kept it, with no reply.
- **Buttons.** The answer's buttons become text quick replies (the label, at most 20 characters; the kind as the
  payload); its suggestions too (payload `say`: tapped, they are what the person typed). Right after the person chose
  SMS or e-mail, the reply offers their own number (`user_phone_number`) or address (`user_email`) in one tap; Meta
  shows it only when their profile has one, and they can always type it.
- **No text.** A photo, a voice message or a file alone is kept as `[photo]`, `[voice message]`, … first, then Skye
  asks for words.
- **Kept, not answered.** An echo from the Page's inbox is kept as the agent's message (never sent back), and Skye
  leaves the thread to the inbox for 30 minutes: the person's messages meanwhile are kept, not answered. So is a
  message heard in standby.
- **Delivered again.** Redis keeps each event's mark (`msgr:event:<mid>`: `pending` for 2 minutes, then the replies
  given, a day): a delivery made again gets the same replies and no second turn; one made while the first is still
  answered gets 409. An event that fails clears its mark, so the next delivery is answered. Redis down: answered
  without the mark (`no_dedupe`).

## The API → the gateway

`POST {MESSENGER_GATEWAY_URL}/v1/send`, signed as above, for what is stored for a Messenger chat outside a turn: an
agent's message from the panel, the "joined" line, anything else.

```json
{"page_id": "…", "psid": "…", "messages": [{"text": "…", "quick_replies": […]}], "tag": "HUMAN_AGENT"}
```

`tag` only once Meta approved Human Agent (the owner's decision); no other tag is taken. Unknown fields: 400. 200
`{"sent": n}`, or `{"sent": n, "failed": {"kind", "code", "subcode"}}` when Meta refused one: `window_closed` (the
person last wrote over 24 hours ago), `unavailable` (blocked the Page, gone), `thread_owned` (the Page's inbox owns
the thread), `token` (the Page's token is wrong or expired), `rate`, `temporary`, `other`. 401 a call not signed by
the API, 404 a Page the gateway has no token for.

The API sends a chat's rows one after another (an agent's "joined" line and their message never cross). Where a
chat answers to is in Redis (`msgr:conv:<conversation id>`, 30 days, renewed by each event); an agent's message to a
chat Redis no longer knows reads the chat's metadata. When an agent's message did not go out, the agent sees a line
in the chat, stored and never sent: past the 24 hours "reach them by SMS or email" (the owner's default until they
decide on Human Agent), unreachable the same, the inbox's thread, or "try again".

## Sending (the gateway)

`POST https://graph.facebook.com/v26.0/me/messages`, the Page's token in `Authorization: Bearer` (never in the
address), `messaging_type: RESPONSE` (`MESSAGE_TAG` with a tag). A text over 2,000 characters goes as several
messages, cut at a paragraph, a line, a sentence or a space, the quick replies on the last; at most 13 quick replies,
titles of 20 characters. One person's sends one after another. Rate limits and Meta's own trouble are tried again
(2, 5, 15 seconds); the rest is counted by kind.

## Redis (the API's)

| Key | Type | Holds | Lives |
|---|---|---|---|
| `msgr:event:<mid or kind:page:psid:time>` | string | `pending`, then the replies given (JSON) | 2 min, then a day |
| `msgr:conv:<conversation id>` | string | `{"page_id", "psid"}` | 30 days, renewed by each event |
| `msgr:ref:<visitor id>` | string | a referral before the first message | 7 days |
| `msgr:queued:<conversation id>` | string | "connecting you with a specialist" was said | 30 min |

## Health

The gateway's `GET /health`: `ready` (every variable set, never their values), the Pages it has tokens for, the
queues' depth and capacity, and the counters: `events`, `forwarded`, `replies_sent`, `agent_sent`, `bad_signature`,
`unreadable`, `too_large`, `unknown_page`, `duplicates`, `queue_full`, `own_echoes`, `skipped`, `forward_failed`,
`forward_refused`, `send_failed` with `window_closed`, `unavailable`, `thread_owned`, `token_failed`,
`rate_limited`, `action_failed`, `bad_calls`, `dropped_at_shutdown` (a deploy that ended with events queued).

The API's `/health/detail` `messenger`: `enabled`, `events`, `turns`, `replies`, `duplicates`, `still_answering`,
`turns_failed`, `turns_refused`, `kept_unanswered`, `inbox_replies`, `attachments`, `referrals`, `no_dedupe`,
`bad_signature`, `sent`, `send_failed` with `window_closed`, `unavailable`, `thread_owned`, `gateway_down`, `no_target`.

## The gateway's environment

`PORT`, `META_APP_ID`, `META_APP_SECRET`, `META_VERIFY_TOKEN`, `META_PAGE_ID_BBC`, `META_PAGE_TOKEN_BBC`,
`META_PAGE_ID_BCT`, `META_PAGE_TOKEN_BCT`, `META_GRAPH_VERSION` (v26.0), `API_URL`, `MESSENGER_GATEWAY_SECRET`. The
secrets live only in the service's variables: never in code, Git, chat, e-mail or Telegram. A token that leaked is
revoked from the System User and replaced.
