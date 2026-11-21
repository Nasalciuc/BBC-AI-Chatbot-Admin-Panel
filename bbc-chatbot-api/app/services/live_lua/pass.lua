-- pass.lua: may this visitor id have a pass to bbc-live (the API, POST /chat/live)? Only the API runs it.
-- An id gets a pass without proof once; after that, while its last pass lives, only by showing that pass,
-- so an id someone saw in the panel's list is not theirs. New ids are limited per address and hour.
-- KEYS[1] live:passed:<vid>   KEYS[2] rl:livepass:<address>:<hour>
-- ARGV[1] 1 when the request showed a pass of this visitor, 0 otherwise   ARGV[2] how long a pass binds
-- its id (seconds)   ARGV[3] new visitor ids an address may get passes for in an hour
--  1 hand a pass   -1 the id has a pass already and none was shown   -2 too many new ids from this address
if ARGV[1] == '1' then
  redis.call('set', KEYS[1], '1', 'EX', tonumber(ARGV[2]))
  return 1
end
if redis.call('exists', KEYS[1]) == 1 then return -1 end
local n = redis.call('incr', KEYS[2])
if n == 1 then redis.call('expire', KEYS[2], 3600) end
if n > tonumber(ARGV[3]) then return -2 end
redis.call('set', KEYS[1], '1', 'EX', tonumber(ARGV[2]))
return 1
