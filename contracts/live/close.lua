-- close.lua: the visits gone quiet go to the queue (bbc-live, every 30 s; contracts/live.md "The visit registry").
-- KEYS[1] live:reg   KEYS[2] live:ended
-- ARGV[1] now (unix seconds)   ARGV[2] quiet (seconds)   ARGV[3] an ended visit's life in the queue (seconds)
-- ARGV[4] at most this many visits
-- Returns {ended, lost}: lost is a visitor listed as open whose visit ran out before it ended (nothing to queue).
-- live:reg:<vid> and live:ended:<vid>:<first_at> keys are built here: one Redis, not a cluster.
local ids = redis.call('zrangebyscore', KEYS[1], '-inf', tonumber(ARGV[1]) - tonumber(ARGV[2]), 'LIMIT', 0, tonumber(ARGV[4]))
local closed, lost = 0, 0
for _, vid in ipairs(ids) do
  local key = 'live:reg:' .. vid
  local first = redis.call('hget', key, 'first_at')
  if first then
    local dest = 'live:ended:' .. vid .. ':' .. first
    if redis.call('exists', dest) == 1 then dest = dest .. ':' .. ARGV[1] end
    redis.call('rename', key, dest)
    redis.call('expire', dest, tonumber(ARGV[3]))
    redis.call('rpush', KEYS[2], dest)
    closed = closed + 1
  else
    lost = lost + 1
  end
  redis.call('zrem', KEYS[1], vid)
end
return {closed, lost}
