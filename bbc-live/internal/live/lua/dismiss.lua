-- dismiss.lua: the visitor said "not now" to a hello (bbc-live, POST /v1/dismiss): nobody claims them
-- for ARGV[2] seconds, the claim goes, and what waited for them is dropped.
-- KEYS[1] live:dismissed:<vid>   KEYS[2] live:claim:<vid>   KEYS[3] live:inbox:<vid>
-- ARGV[1] vid   ARGV[2] seconds
-- Returns the agent id that held the visitor, or nil.
redis.call('set', KEYS[1], '1', 'EX', tonumber(ARGV[2]))
local holder = redis.call('get', KEYS[2])
if holder then
  redis.call('del', KEYS[2])
  redis.call('srem', 'live:claims:' .. holder, ARGV[1])
end
redis.call('del', KEYS[3])
return holder
