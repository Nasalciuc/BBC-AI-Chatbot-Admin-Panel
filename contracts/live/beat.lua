-- beat.lua: a visitor is on the site (bbc-live, POST /v1/beat).
-- KEYS[1] live:v:<vid>   KEYS[2] live:visitors
-- ARGV[1] vid   ARGV[2] now (unix seconds)   ARGV[3] the visit's life after this beat (seconds)
-- ARGV[4..] field, value, field, value ... (every field, every beat: an empty value clears it)
redis.call('hsetnx', KEYS[1], 'first_at', ARGV[2])
local pairs_ = {'last_at', ARGV[2]}
for i = 4, #ARGV do pairs_[#pairs_ + 1] = ARGV[i] end
redis.call('hset', KEYS[1], unpack(pairs_))
redis.call('expire', KEYS[1], tonumber(ARGV[3]))
redis.call('zadd', KEYS[2], tonumber(ARGV[2]), ARGV[1])
redis.call('zremrangebyscore', KEYS[2], '-inf', tonumber(ARGV[2]) - tonumber(ARGV[3]))
return 1
