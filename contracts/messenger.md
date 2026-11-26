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
- `POST /webhook`: while a setting is missing (`/health` `ready: false`: the app secret, the verify token, the
  app's id, a Page with its id and token, the API's address, the shared secret), 503 before anything is read
  (`not_ready`): Meta delivers it again once the gateway is set up. Without the app's id its own messages would come
  back as echoes the API takes for the Page inbox's. `X-Hub-Signature-256: sha256=<hex
  HMAC-SHA256(META_APP_SECRET, raw body)>`, else 403. Body over 1 MiB: 413. Signed but not `"object": "page"`: 200,
  counted (`unreadable`). Each event, oldest first (Meta does not promise the order in a batch): a Page without a
  token is skipped (`unknown_page`); the app's own echoes are left out (`own_echoes`); read, delivery and reaction
  events are skipped; an event already taken (by Meta's message id, 2 hours, at most 200,000 kept, the oldest
  forgotten first; this process's memory) is skipped (`duplicates`); the rest go to the person's worker (the Page
  and PSID pick it: one person's events in order). The check, the queueing, the mark and the holds below are one
  step for the whole delivery: the same delivery arriving twice at once is taken once, and no other delivery is
  decided in the middle of it. A full queue answers 503 and Meta delivers the batch again (`queue_full`); an event
  is marked taken only once queued, so the part already queued comes back as duplicates and the rest is taken. Each
  event of the part not taken then takes its place among its person's refused events, by Meta's time. A delivery of
  theirs waits for those of them from before it (503, `held`, and it takes its own place): the earliest is taken
  first, then the next, so none overtakes an earlier one, whichever Meta delivers first; one from before them all is
  taken at once, never made to wait behind a later one. Stopping (a deploy): 503 (`closing`). 200 at once otherwise:
  Meta wants it within 5 seconds, after 15 minutes of failures it warns, after an hour it unsubscribes the app from
  the Page, so the gateway never waits for the API before answering Meta.

  The order is kept within bounds, past which Messenger staying up wins over it: a person is waited for 15 minutes
  at most in all, from the first refusal of their wait, however Meta delivers their events again (an event refused
  again, or one more of theirs refused, never starts it again; what the wait still had is `hold_expired`, counted at
  the next delivery of its person or by the minute's cleanup; waited for near Meta's hour, the person's own
  deliveries failing would put the whole app at risk), at most 10,000 people wait at once and each for 100 events
  (memory: what does not fit is not waited for, `hold_full`; a new person who finds no room first has the waits past
  their 15 minutes dropped, counted in `hold_expired`, so only live waits fill it). Past them, a later event may reach
  the API before a refused one, which the API still answers once.

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
batch go on its last message). 409: the same event, or an earlier one of the person's, is still being answered (or
its replies are still to be sent). 422: not an event. 503 with
`X-Messenger-Kept: 1`: the event failed, and the API keeps it and answers it later itself (below). 503 with
`X-Messenger-Off: 1`: the channel is off, nothing is kept. Any other 503: the event failed and is kept nowhere.

The gateway keeps an event until the API has it. A network failure, a 5xx without `X-Messenger-Kept` (the API's
proxy while it restarts), 429 or 409: tried again after 2, 5 and 15 seconds, then every minute until an hour has
passed since the first try (`forward_failed` after that; the events behind it in its worker's queue wait, in
order; no call starts once the hour is over, the last wait cut to what is left of it). Kept by the API: tried
after 2, 5 and 15 seconds only, then left to it (`forward_kept`). The channel off: final (`api_off`); another
4xx: final (`forward_refused`). An answer other than 2xx is read by its status and headers alone, so one whose
body breaks off is still what it says. While the API thinks, the person sees the message read and Skye
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
  Skye's ask for words, an echo) is saved before anything answers it, and never twice: the row's id is made from
  the event's key and the row's part (UUID v5), so an insert made again (a delivery made again, the recovery, after
  an insert whose answer was lost) is refused by the database and the row stored before is read back and used
  (`saved_once`), with no mark in Redis to lose; the chat's activity clocks, which the lost insert never moved,
  catch up to that row's own time, never back from a newer message's (one guarded UPDATE a clock). Not saved, or
  stored before and not readable: the event fails.
- **Delivered again.** Redis keeps each event's mark (`msgr:event:<mid>`: the turn's claim, `pending:<its own
  token>`, for 2 minutes, renewed every 40 seconds while its turn runs and only while it still holds that token,
  then the replies given and who sent them, a day): a delivery made again gets the same replies and no second turn
  (none when the API sent them itself); one made while the first is still answered, however long its turn takes,
  gets 409. A turn starts only on a claim it took: one let go of between the delivery's SET NX and its read (run
  out, or its turn failed) is taken again, and one let go of each time it is looked at reads as still answered
  (409). An event that fails clears its claim (only its own), so the next delivery is answered. A turn whose
  claim ran out (its renewal failing) and whose event another delivery or pass took since never renews, clears or
  answers over theirs: its answer is not written (`claim_lost`), and a recovery pass's is never sent beside theirs.
  An answer Redis did not take as its turn ended is held by its process and written at its next minute, in place of
  the turn's claim or of nothing (`answer_unkept`): a delivery made again meanwhile gets it from that process, with
  no second turn, and the process keeps the event's claim (a new expiry each minute) until it is written, so no other
  delivery or pass answers the event again; a newer one of the event, held meanwhile, is never dropped by an older
  one's write. A shutdown stops the recovery first (5 seconds at most), so none of its turns holds one after, then
  writes what the process holds, each one tried whatever Redis said of the one before; one Redis still refuses then
  goes with it (`answer_dropped`), and its event may be answered again once its claim runs out. Redis down: answered
  without the mark (`no_dedupe`).
- **Answered later.** From the moment it is taken until it is answered, an event is in the inbox (`msgr:inbox`, its
  JSON beside it, 2 days). One whose turn failed is answered by the scheduler's minute (`messenger_recover`) once 10
  minutes have passed (longer than the gateway's own tries of a kept event), its replies sent through `/v1/send`; the
  gateway down then, the same replies go the next minute, without a second turn. Its replies go only once its answer
  is written: one Redis did not take waits for its process to write it, the next minute, and then goes, so a send
  that never reached the gateway goes again from the answer written, never from a new turn. The pass reads and writes each
  event's mark and its place in the inbox through its own Redis client: a cooldown begun meanwhile (another call's
  failure, process wide) never reads as an event with no mark, and Redis failing stops the pass, the event left for
  the next one. One process at a time (a lock in
  Redis, 5 minutes, held under the pass's own token, renewed every 100 seconds while the pass runs and let go only
  while it is still that pass's, so a slow turn or send never lets another pass in beside it and a pass never frees
  another's); a pass starts nothing new after 2 minutes, nor once its lock is found lost (what it is on goes on: a
  send's mark keeps any reply from going twice), and the agents' notes go first within 30 seconds and 20 of them at
  most, each one cut at the 30 seconds (its note's id makes saving it again harmless), the outbox gone through in
  parts, the deliveries looked at in the part the passes are on kept by their ids until it is gone through (a part
  can be any size, and not always the same one for a cursor: HSCAN's COUNT is a hint), so a pass goes on from those
  not looked at yet whatever left the part meanwhile, the events always get their part, and a note that keeps
  failing, or hangs, never keeps the others from their turn. After 60
  tries, or unreadable, the event is given up
  (`recover_given_up`, logged without its words). Redis down: not kept (`not_kept`), the gateway's tries only. The
  recovery sends again only what never reached the gateway (`gateway`); replies the gateway had and never answered for
  (`unknown`) are never sent a second time.
- **Sent once.** Each send of an event's replies the API makes (the recovery's, or an earlier event's below) is
  written down before it starts (`msgr:inbox:sending:<key>`: when, and by which process), by one sender only (SET
  NX: a pass whose turn of the event ends after another's began a send, or after it sent them and the event left the
  inbox, sends nothing), and once it is over (`done`); the mark stays until it expires, past the event's leaving the
  inbox. A delivery of a kept event from the gateway takes the same right, with its answer, in one step and only
  while it still holds the event's claim, before its replies go back to the gateway (`gateway:<when>:<id>`, stamped
  each time it is taken, its id saying whose it is): a recovery
  pass of the event that lost its claim mid-turn sends nothing beside it, a delivery that lost its claim gives the
  gateway none, and one whose send the API began first gets none either: its own answer is kept as the API's to send
  while that send is on its way (never one with none: should that send go nowhere, the next pass sends from it), one
  with none once it is over. One whose giving Redis did not answer (the right and the answer maybe written, maybe
  not) gives the gateway none (409), the event kept, and its process holds the giving: made again, with the same
  right (its id; stamped with when it is made again, never the first try's time), at its next minute or when the
  event is delivered again (a right found its own is a run whose answer was
  lost: given), so a delivery made again gets the event's answer from Redis, this one's, or none when another
  delivery's or the API's send stands; never from the process while Redis fails. Two turns of the event in one
  process whose answers Redis did not take (one whose claim ran out while it ran, and the delivery that took the
  event since) are both held, never one over the other: each is tried in turn when the one before finds another's
  claim there, the first written stands, the others are dropped then (`claim_lost`), and neither is given from the
  process meanwhile. The right reads as on its way for 150 seconds (the gateway's own call to the API, 75 seconds,
  begun before the right, then its 60 seconds to send), then as over: never sent by the API. A delivery made again of
  replies given to the gateway (it never got that answer, or stopped before sending them, and sends them now) gets
  the same replies, the right taken again from then while the event is kept; the event let go of since and an event
  of the person's taken after the right, none (`late_dropped`: never after its replies); Redis failing, 409. An event
  given to the gateway while a later one of the person's waits in the inbox (or while one could not be looked up:
  never taken for none) stays there until then, so the later one waits for it. An event found in the inbox with a send written down is never sent again: one over (the inbox not cleared
  after, a process stopped or Redis failed) just leaves it; one begun more than 90 seconds ago and never over, by a
  process no longer alive (it stopped in the middle of it: maybe with the person), leaves it too, counted
  (`recover_unsure`). Either is let go of only while its mark is still the one read (its process, back meanwhile, may
  have cleared it, and the event is sent from its answer; or the gateway, given its replies again, sends them from
  then): kept so, or not known (Redis failing), its person's later events wait behind it; one begun lately, or by a process still alive, is on its way, and the event waits: every old
  mark of a live process is one it knows of and settles at each of its minutes, whether it holds the lock or not. Only
  a send that never reached the gateway clears its mark (compare and delete: only its own) and goes again, its
  event's answer written back first where it went meanwhile (its day over, or Redis let it go; never over another's
  claim or answer), so the same replies go, with no second turn; its mark
  not cleared then (Redis failing), or written with its answer lost, its process clears it at its next minute, or
  when it next looks at the event, however long Redis took, with the answer written back, in one step and only while
  the event is still in the inbox (let go of meanwhile, it keeps the mark and sends them late, below); a send that ended and whose end could not be written has
  its event let go of then (out of the inbox, never sent again). A process that stopped first leaves its marks to read
  as begun: never sent twice. A process cut off from Redis past its alive mark while it runs, whose event another
  process let go of so meanwhile, sends the replies it knows never went out itself once Redis answers, once
  (`sent_late`), in the person's order: it puts the event back in the inbox first, under a new mark of its own (on
  its way for every process), so a later event of theirs waits behind it, then lets it go again; only while no event
  of theirs was taken after its send began (each take keeps when the person's latest was,
  `msgr:inbox:last:<visitor id>`: never Meta's time, which two events can share). One taken since, its replies maybe
  out already: never sent after them (`late_dropped`), nothing waiting on it. A putting back Redis ran and lost the
  answer of leaves the event in the inbox under the new mark, with nothing sent: its process holds the new mark beside
  the old one, reads the mark at its next minute, and sends the replies then, once (another process reads the new
  mark as on its way meanwhile); one that did not run is tried again from the old mark.
- **The person's order.** The inbox also keeps each person's events by when they were taken
  (`msgr:inbox:who:<visitor id>`). An event never runs its turn while an earlier one of its person's is still in the
  inbox: that one is answered first, at once, and its replies sent through `/v1/send` before this one's turn
  (`answered_first`), so they are never lost if this one then fails; this one's replies go back to the gateway as
  usual. That one's answer is written first, then its send: one whose answer Redis did not take sends nothing, and
  this one waits while its process writes it. One still being answered elsewhere, or answered with its replies not
  sent yet (the gateway could not be reached: the recovery sends them, without a second turn), or being sent now,
  makes this one wait (409: the
  gateway tries again); one failing again makes this one fail too, kept behind it, and so does one Redis could
  not read (never taken for one gone: this one is never answered past it). One there and unreadable is left to the
  recovery to give up. Redis down (this one not kept): there is no inbox to look in. Once this one is kept, a Redis
  cooldown begun since (another call's failure, process wide, while Redis may answer) never reads as "nothing
  earlier": they are looked up all the same, and Redis failing makes this one fail, kept. What an answered event's
  mark and its place in the inbox say is written through a cooldown too, so the recovery never answers it a second
  time. The recovery goes oldest first, and a person whose event still waits has their later ones wait that minute,
  untried.

## The API → the gateway

`POST {MESSENGER_GATEWAY_URL}/v1/send`, signed as above, for what is stored for a Messenger chat outside a turn: an
agent's message from the panel, the "joined" line, anything else.

```json
{"page_id": "…", "psid": "…", "messages": [{"text": "…", "quick_replies": […]}], "tag": "HUMAN_AGENT"}
```

`tag` only once Meta approved Human Agent (the owner's decision); no other tag is taken. Unknown fields: 400. 200
`{"sent": n, "parts": m, "parts_sent": k}`: n messages went out whole; their texts made m Messenger messages (a long
one is several), k of which went out, in order. With `"failed": {"kind", "code", "subcode"}` when one did not:
`window_closed` (the person last wrote over 24 hours ago), `unavailable` (blocked the Page, gone), `thread_owned`
(the Page's inbox owns the thread), `token` (the Page's token is wrong or expired), `rate`, `temporary` (Meta said
it did not send it), `unknown` (maybe with the person, never sent again: Meta had the whole call and its answer
never came, or a 5xx that does not say), `timeout` (the send's 60 seconds ran out before the next call could start:
nothing in flight), `other`. The 60 seconds count the wait for the person's other sends too, so the answer always
comes back (the HTTP server's own limit is 75), except in a deploy: the gateway gives the requests still running 25
seconds, and a send cut then has no answer, its outcome unknown to the API even when some pieces went out (the API's
`unknown`). `parts` is counted before the wait for the person's other sends, so a send that never got its turn still
says how many there were. 401 a call not signed by the API, 404 a Page the gateway has no token for.

The API sends a chat's rows one after another, in the order they were stored (a chat's rows are stored one at a time
in each process, and a row's place is taken the moment the database stores it, before the chat's activity clocks and
the live push, even when the request that stored it was cancelled meanwhile. Outside a Messenger turn (an agent's
message, a "joined" line) a row's save is one piece of work that holds the chat's order to its end: the insert, its
retry under the same id (a row stored with its answer lost comes back, stored before), the `unsure` mark when its
save is still not known (the recovery minute looks its row up while the process lives: stored, its agent is told it
was not sent), and what its place leaves to do (its delivery crossed out, its agent told). A request cancelled
meanwhile only stops waiting for it, at once: never the save, whose insert may land all the same, and the chat's next
row waits for the save, never for a request. A turn's rows join the turn's answer, a row stored while a cancel waits
included. An agent's "joined" line and their message never cross, however long an insert's answer takes); each
row ends only after the chat's previous one, so one with nothing to send (its chat not read, kept for the minute, or
going nowhere) never lets the next one go beside the one before it; a row waiting for the chat's previous one is not sent if the channel was turned off meanwhile
(`off_skipped`). Where a
chat answers to is in Redis (`msgr:conv:<conversation id>`, 30 days, renewed by each event); an agent's message to a
chat Redis no longer knows reads the chat's metadata, and one whose chat could not be read is never taken for a site
chat's: it stays written down as `unsure` (`target_unread`), and the recovery minute looks the chat up again, its
process alive or not, and tells the agent it was not sent (a site chat's is crossed out then, without a word; at a
shutdown it is left for the next process's minute). When an agent's message did not go out, the agent sees a line
in the chat, stored and never sent: past the 24 hours "reach them by SMS or email" (the owner's default until they
decide on Human Agent), unreachable the same, the inbox's thread, "maybe not sent" (`unknown`: send it again if the
client doesn't reply), "only the first k of m pieces" (`partly_sent`: send the rest, not the whole message), "the
first k of m pieces, and maybe the next" (k pieces out, the next `unknown`: ask the client what they got before
sending the rest), or "try again". The API's own call to `/v1/send` with no answer: known to have gone nowhere
(no connection to write it on: the connection refused or not made in time, none free in the pool; or turned away
with a 3xx or 4xx: the gateway's own refusals are a 4xx, before it sends anything), nothing went out (`gateway`, "try
again"); anything else (a write that failed or ran out of time, whose bytes may reach the gateway whole all the same;
a lost connection, the 80 seconds over, any 5xx: the gateway never answers one, and a proxy may once the gateway had
the call), `unknown`.

A shutdown gives the sends on their way 8 seconds, then 2 for the notes; each agent whose message did not go out then
is told (not sent, or maybe not when it was with the gateway already), and a message saved after it began is told at
once (`cut_at_shutdown`). An agent's message is written down in Redis (`msgr:outbox`) before it is stored, under the
id its row gets (whatever the chat: a site chat's is crossed out once where it goes is looked up), until it went out
or its agent was told, so nothing a stop or a cancelled request can cut comes before it, and a note the shutdown
could not save is not lost. One whose insert gave no row (it may have been committed, its answer lost) is asked again
under the same id: the row stored before comes back and goes out; still not known, it stays written down as
`unsure`, and the recovery minute looks its row up even while its process is alive: stored, the agent is told it was
not sent; not stored, it is crossed out without a word. A write whose answer was lost is taken as written. It goes to the gateway only once its being sent is written down too (Redis refusing that: not sent, "try
again"), so a stop never says "not sent" of one that may be with the client; a send whose end was known before a stop
says that end (sent: nothing to tell; refused: its own note). Once the stopped process's alive mark is gone
(`msgr:alive:<process>`, 5 minutes, renewed each minute and by each message written down), the next process's
recovery minute tells the agent (`told_later`), once (the note's id is the delivery's), when the chat is on Messenger
and the row is found stored (one left before that was looked up is looked up then: a site chat's says nothing, nor
one whose row never was stored; a chat or a row not read is looked at again the next minute). A note that could not
be saved in a live process stays written down too (`untold`), and the minute saves it; after 2 days, or unreadable,
given up (`outbox_given_up`). Redis down: not written down (`outbox_unsaved`), and the message still goes out; only
the note a stop would leave its agent can be lost then.

## Sending (the gateway)

`POST https://graph.facebook.com/v26.0/me/messages`, the Page's token in `Authorization: Bearer` (never in the
address), `messaging_type: RESPONSE` (`MESSAGE_TAG` with a tag). A text over 2,000 characters goes as several
messages, cut at a paragraph, a line, a sentence or a space. The quick replies go on the batch's last message
(Messenger shows them on the newest one only): those of the last reply that has any. At most 13 quick replies,
titles of 20 characters. One person's sends one after another. Rate limits (a Graph code, or HTTP 429 with no code)
and Meta saying it did not send it (code 2, code 1200) are tried again (2, 5, 15 seconds), and so is a network
failure before the call could be with Meta (no try of it had a connection, or each one that did was told its write
failed and none that it wrote: the transport never sends a call again once any of it went out, its body not one it
can read twice, though it may begin one again after a try that sent nothing, and a try's end can be told after the
next one began, so the tries are counted, never paired). A peer resetting the stream with PROTOCOL_ERROR is no
refusal (RFC 9113 says that of REFUSED_STREAM and GOAWAY alone). A call Meta may have had whole is never tried
again (the Send API takes no key that would make a second send the same message): one with a try whose write was
not told failed (the transport writes apart from the call's own wait, and a deadline can end the wait before the
write's end is told; the 10 seconds over included), and any other 5xx (500 with code 1 or none, 502, 503 or 504
that do not say: a server or a proxy failed once it may have had the call, RFC 9110; a proxy answers 503 too when
the server it passed the call to went away): `unknown` (`unconfirmed`). A refusal's body is believed only whole: a
code read from a body cut short, longer than the 64 KiB read, or not Meta's shape whole (a field of the wrong type)
says nothing, and only the status decides. A 200 is a message sent, whatever its body.
A call starts only while 10 seconds (the Meta client's own limit) are left of the send's time. An event's replies
have the same 60 seconds as an agent's send, the wait for the person's other sends and the tries included, so they
are over within the 150 seconds the API reads the gateway's right to send them as on its way, from when it gave them
back (or gave them again to a delivery made again): the call's 75 seconds, then these 60 (`timeout`, counted as
`out_of_time`, once the time is up). The rest is counted by kind.

## Redis (the API's)

| Key | Type | Holds | Lives |
|---|---|---|---|
| `msgr:event:<mid or kind:page:psid:time>` | string | the turn's claim, `pending:<its token>`, then `{"replies", "by": "gateway" \| "api"}` | 2 min, then a day |
| `msgr:inbox` | sorted set | the events taken and not answered yet, by when they were taken | until answered |
| `msgr:inbox:<key>` | string | one of them, as the gateway handed it over | 2 days |
| `msgr:inbox:tries:<key>` | string | the recovery's tries of it | 2 days |
| `msgr:inbox:sending:<key>` | string | the API's send of its replies: `<when it began>:<process>`, then `done`; or the gateway's right to send them, `gateway:<when>:<id>` (stamped each time it is taken) | 2 days |
| `msgr:inbox:who:<visitor id>` | sorted set | one person's events in the inbox, by when they were taken | 2 days, renewed |
| `msgr:inbox:last:<visitor id>` | string | when the person's latest event was taken (a first take, a delivery made again, or the recovery's), answered since or not | 2 days, renewed |
| `msgr:recover:lock` | string | the pass's own token: one process recovers at a time | 5 min, renewed while the pass runs |
| `msgr:outbox` | hash | agents' messages on their way: `{"cid", "boot", "state", "at", "row"?, "note"?}` a delivery (`state`: `waiting`, `sending`, `untold`, `unsure`) | until sent or told |
| `msgr:outbox:cursor` | string | the part of the outbox the recovery is on (an HSCAN cursor) | 2 days |
| `msgr:outbox:seen` | set | the deliveries looked at in that part, until it is gone through | 2 days |
| `msgr:alive:<process>` | string | a process that writes deliveries down is alive | 5 min, renewed |
| `msgr:conv:<conversation id>` | string | `{"page_id", "psid"}` | 30 days, renewed by each event |
| `msgr:ref:<visitor id>` | string | a referral before the first message | 7 days |
| `msgr:queued:<conversation id>` | string | "connecting you with a specialist" was said | 30 min |

## Health

The gateway's `GET /health`: `ready` (every variable set, never their values), the Pages it has tokens for, the
queues' depth and capacity, `pending` (events taken and not done with), and the counters: `events`, `forwarded`,
`replies_sent`, `agent_sent`, `bad_signature`, `unreadable`, `too_large`, `unknown_page`, `duplicates`,
`queue_full`, `held`, `hold_expired`, `hold_full`, `closing`, `not_ready`, `own_echoes`, `skipped`, `forward_failed`,
`forward_kept`, `forward_refused`, `api_off`, `send_failed` with `window_closed`, `unavailable`, `thread_owned`,
`token_failed`, `rate_limited`, `unconfirmed`, `out_of_time`, `action_failed`, `bad_calls`, `dropped_at_shutdown`
(a deploy that ended with events not done with: queued, or still with their worker).

A deploy (SIGTERM): the webhook takes nothing more (503), and the queued events go to the API while the HTTP server
waits for the requests still running (an agent's send): both share the 25 seconds, so a long send never leaves
the queues without time.

The API's `/health/detail` `messenger`: `enabled`, `events`, `turns`, `replies`, `duplicates`, `still_answering`,
`turns_failed`, `turns_refused`, `kept_unanswered`, `inbox_replies`, `attachments`, `referrals`, `no_dedupe`,
`saved_once`, `not_kept`, `recovered`, `recover_waiting`, `recover_given_up`, `answered_first`, `recover_unsure`,
`claim_lost`, `answer_unkept`, `sent_late`, `late_dropped`, `answer_dropped`, `bad_signature`,
`sent`, `send_failed`
with `window_closed`, `unavailable`, `thread_owned`, `unconfirmed`, `partly_sent`, `gateway_down`, `no_target`,
`target_unread`, `off_skipped`, `cut_at_shutdown`, `outbox_unsaved`, `told_later`, `outbox_given_up`.

## The gateway's environment

`PORT`, `META_APP_ID`, `META_APP_SECRET`, `META_VERIFY_TOKEN`, `META_PAGE_ID_BBC`, `META_PAGE_TOKEN_BBC`,
`META_PAGE_ID_BCT`, `META_PAGE_TOKEN_BCT`, `META_GRAPH_VERSION` (v26.0), `API_URL`, `MESSENGER_GATEWAY_SECRET`. The
secrets live only in the service's variables: never in code, Git, chat, e-mail or Telegram. A token that leaked is
revoked from the System User and replaced.
