-- visit.lua: one snapshot of a visit for the registry (bbc-live, POST /v1/visit; contracts/live.md "The visit registry").
-- KEYS[1] live:reg:<vid>   KEYS[2] live:reg   KEYS[3] live:ended
-- ARGV[1] vid   ARGV[2] now (unix seconds)   ARGV[3] quiet: a visit with no snapshot for longer has ended
-- ARGV[4] an open visit's life after a snapshot (seconds)   ARGV[5] an ended visit's life in the queue (seconds)
-- ARGV[6] at most this many pages a visit   ARGV[7] the page's id   ARGV[8] the page (JSON, made by bbc-live)
-- ARGV[9] the card's state (shown, dismissed, engaged or '')   ARGV[10] when the card showed (unix seconds, or '')
-- ARGV[11] when the card was answered (unix seconds, or '')
-- ARGV[12] n, then n field, value pairs kept from the visit's first snapshot that has a value for them
-- then m, then m field, value pairs where the latest value wins; an empty value never writes
-- Returns 1, or 2 when the visit had gone quiet: it went to the queue first and this snapshot starts a new one.
-- live:ended:<vid>:<first_at> keys are built here: one Redis, not a cluster.
local key, reg, ended = KEYS[1], KEYS[2], KEYS[3]
local vid, now, quiet = ARGV[1], tonumber(ARGV[2]), tonumber(ARGV[3])
local out = 1
local last = tonumber(redis.call('hget', key, 'last_at') or '')
if last and now - last > quiet then
  local dest = 'live:ended:' .. vid .. ':' .. (redis.call('hget', key, 'first_at') or ARGV[2])
  if redis.call('exists', dest) == 1 then dest = dest .. ':' .. ARGV[2] end
  redis.call('rename', key, dest)
  redis.call('expire', dest, tonumber(ARGV[5]))
  redis.call('rpush', ended, dest)
  redis.call('zrem', reg, vid)
  out = 2
end
redis.call('hsetnx', key, 'first_at', ARGV[2])
redis.call('hset', key, 'last_at', ARGV[2])
-- The pages, in the order they were first heard of; a page past the limit is counted, not kept.
local pid = ARGV[7]
local pages = redis.call('hget', key, 'pages') or ''
local known = string.find(',' .. pages .. ',', ',' .. pid .. ',', 1, true) ~= nil
if not known then
  local n = 0
  for _ in string.gmatch(pages, '[^,]+') do n = n + 1 end
  if n < tonumber(ARGV[6]) then
    redis.call('hset', key, 'pages', pages == '' and pid or (pages .. ',' .. pid))
    known = true
  elseif redis.call('hget', key, 'over_last') ~= pid then
    redis.call('hset', key, 'over_last', pid)
    redis.call('hincrby', key, 'pages_over', 1)
  end
end
if known then redis.call('hset', key, 'pg:' .. pid, ARGV[8]) end
-- What came of the card only moves forward: shown, then dismissed, then engaged.
local rank = {shown = 1, dismissed = 2, engaged = 3}
local state = ARGV[9]
if rank[state] and rank[state] > (rank[redis.call('hget', key, 'card_state') or ''] or 0) then
  redis.call('hset', key, 'card_state', state)
end
if ARGV[10] ~= '' then redis.call('hsetnx', key, 'card_at', ARGV[10]) end
if ARGV[11] ~= '' then redis.call('hsetnx', key, 'card_answer_at', ARGV[11]) end
local i = 12
local n = tonumber(ARGV[i])
for j = 1, n do
  local v = ARGV[i + 2 * j]
  if v ~= '' then redis.call('hsetnx', key, ARGV[i + 2 * j - 1], v) end
end
i = i + 2 * n + 1
local m = tonumber(ARGV[i])
for j = 1, m do
  local v = ARGV[i + 2 * j]
  if v ~= '' then redis.call('hset', key, ARGV[i + 2 * j - 1], v) end
end
redis.call('expire', key, tonumber(ARGV[4]))
redis.call('zadd', reg, now, vid)
return out
