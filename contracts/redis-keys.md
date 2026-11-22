# Redis keys shared between services

The API (Python, `bbc-chatbot-api`) and the live service (Go, `bbc-live`) talk through one Redis.
Redis holds coordination, never the truth: anything here can disappear and the system must
carry on (Supabase is the truth). A key that is not listed here belongs to one service only.

No key or value carries a name, an email or a phone number. Ids are UUIDs.

## "On the site now" (`contracts/live.md`)

The `live:` keys and the `live:inbox` channel are the API's and bbc-live's together, `rl:livepass:` the
API's alone; their table, scripts and lives are in [`live.md`](live.md#redis-keys).

## Conversation locks (`app/services/conversation_lock.py`)

| Key | Type | Value | Life |
|---|---|---|---|
| `lock:<kind>:<conversation_id>` | string | a random token (32 hex chars) of the holder | `PX 15000` |

- Taken with `SET key token NX PX 15000`; another holder waits (up to 10 s) and then goes on
  under its in-process lock.
- Released only by the holder, with this script, which deletes the key when it still holds the
  holder's token:

  ```lua
  if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
  end
  return 0
  ```
- `<kind>` is the kind of work: `lead` (the lead writers), `post_sale` (the send after closing).

## Who is at the desk (`app/services/agent_presence.py`)

| Key | Type | Member | Score |
|---|---|---|---|
| `presence:operators` | sorted set | user id | unix seconds of the last beat |

- Written by the heartbeat on every beat (every 5 s per open panel), only for people who may take
  chats: an operator role (`sales`, `support`), active, chat enabled. Anyone else is removed.
- Present = a score inside the shared queue's window: `now - 90 <= score`
  (`queue_presence_window_seconds`). Readers use `ZRANGEBYSCORE presence:operators <now-90> +inf`
  or `ZCOUNT` with the same bounds.
- Scores older than a day are removed as beats come in.
- Readers must tell "nobody is there" (an empty answer) from "Redis cannot say" (no answer):
  the second falls back to `users.last_seen_at` in Supabase.

## Scheduled jobs that must not run twice (`app/services/conversation_lock.py`)

| Key | Type | Value | Life |
|---|---|---|---|
| `lock:job:<job>` | string | a random token of the process running the job | the job's own TTL |

- `SET NX PX` without waiting: a process that does not get it skips that run. Released with the
  same compare-and-delete script as the conversation locks.

## The follow-up lottery's badge (`app/services/lead_lottery.py`)

| Key | Type | Member | Score |
|---|---|---|---|
| `followup:new:<agent id>` | sorted set | conversation id | unix seconds it was given |

- A member is added when a lead is given to the agent (by the lottery or by a manager) and removed
  when they open it or mark its offer, or when it goes back unconfirmed.
- The heartbeat answers `followups_new = ZCARD`; no answer from Redis means no number, not zero.
  While it is above zero it also answers `followup_newest = ZREVRANGE … 0 0`, the lead given last, which
  the panel's alert opens (opening it confirms it); `null` otherwise.
- The truth is `conversations.assigned_agent_id` and `metadata.followup`; this is only the count.

## The API's own keys (listed so no other service takes the prefix)

| Key | Type | Value | Life |
|---|---|---|---|
| `recent:<user id>` | list | conversation ids, newest first, at most 10 | 30 days after the last open |
| `rl:search:<user id>:<unix minute>` | string | how many message searches that minute | 120 s |

- `recent:` (`app/services/recent_chats.py`): the chat's detail does `LREM 0 id`, `LPUSH id`,
  `LTRIM 0 9`, `EXPIRE` in one transaction; the search box reads `LRANGE 0 9` and reads the chats
  fresh from Supabase. No answer from Redis means no list, not an empty one.
- `rl:search:` (`app/api/search.py`): `INCR` + `EXPIRE 120`; over `SEARCH_MESSAGES_PER_MINUTE` the
  search answers 429. Without Redis each process counts on its own.
