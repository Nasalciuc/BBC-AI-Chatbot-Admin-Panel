-- claim.lua: an agent takes a visitor to say hello first (bbc-live, POST /v1/claim). Atomic: two
-- agents on the same visitor, one wins; an agent holds at most ARGV[3] visitors at a time.
-- KEYS[1] live:v:<vid>   KEYS[2] live:claim:<vid>   KEYS[3] live:claims:<agent>   KEYS[4] live:dismissed:<vid>
-- ARGV[1] agent id   ARGV[2] vid   ARGV[3] most visitors an agent holds   ARGV[4] the claim's life (seconds)
--  1 claimed   0 already this agent's   -1 the visitor left   -2 another agent's
-- -3 this agent holds the most already   -4 the visitor said "not now" lately   -5 the visitor is in a chat
if redis.call('exists', KEYS[1]) == 0 then return -1 end
if redis.call('exists', KEYS[4]) == 1 then return -4 end
if redis.call('hget', KEYS[1], 'has_chat') == '1' then return -5 end
local holder = redis.call('get', KEYS[2])
if holder == ARGV[1] then return 0 end
if holder then return -2 end
local held = 0
for _, v in ipairs(redis.call('smembers', KEYS[3])) do
  if redis.call('get', 'live:claim:' .. v) == ARGV[1] then held = held + 1 else redis.call('srem', KEYS[3], v) end
end
if held >= tonumber(ARGV[3]) then return -3 end
redis.call('set', KEYS[2], ARGV[1], 'EX', tonumber(ARGV[4]))
redis.call('sadd', KEYS[3], ARGV[2])
redis.call('expire', KEYS[3], tonumber(ARGV[4]))
return 1
