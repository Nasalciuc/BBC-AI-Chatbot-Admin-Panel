-- release.lua: the agent lets the visitor go (bbc-live, POST /v1/release; the API, after the tap).
-- KEYS[1] live:claim:<vid>   KEYS[2] live:claims:<agent>   ARGV[1] agent id   ARGV[2] vid
-- 1 released   0 it wasn't this agent's
if redis.call('get', KEYS[1]) == ARGV[1] then
  redis.call('del', KEYS[1])
  redis.call('srem', KEYS[2], ARGV[2])
  return 1
end
return 0
