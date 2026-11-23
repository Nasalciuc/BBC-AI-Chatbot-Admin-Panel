-- beat.lua: a visitor is on the site (bbc-live, POST /v1/beat).
-- KEYS[1] live:v:<vid>   KEYS[2] live:visitors
-- ARGV[1] vid   ARGV[2] now (unix seconds)   ARGV[3] the visit's life after this beat (seconds)
-- ARGV[4..] field, value, field, value ... (every field, every beat: an empty value clears it)
-- has_chat, once '1', stays '1' for the visit's life: a beat sent before the chat started that lands after the one
-- saying it did never takes it back (no beat follows that one). A new visit (the hash ran out) starts without it.
local had_chat = redis.call('hget', KEYS[1], 'has_chat') == '1'
redis.call('hsetnx', KEYS[1], 'first_at', ARGV[2])
local pairs_ = {'last_at', ARGV[2]}
for i = 4, #ARGV, 2 do
  local value = ARGV[i + 1]
  if ARGV[i] == 'has_chat' and had_chat then value = '1' end
  pairs_[#pairs_ + 1] = ARGV[i]
  pairs_[#pairs_ + 1] = value
end
redis.call('hset', KEYS[1], unpack(pairs_))
redis.call('expire', KEYS[1], tonumber(ARGV[3]))
redis.call('zadd', KEYS[2], tonumber(ARGV[2]), ARGV[1])
redis.call('zremrangebyscore', KEYS[2], '-inf', tonumber(ARGV[2]) - tonumber(ARGV[3]))
return 1
