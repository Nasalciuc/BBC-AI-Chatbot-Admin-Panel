-- list.lua: who is on the site now (bbc-live, GET /v1/live).
-- KEYS[1] live:visitors   ARGV[1] since (unix seconds: a beat after it)   ARGV[2] at most this many
-- Returns a flat array, four entries per visitor: vid, the visit's hash (field, value, ...),
-- the agent id holding the visitor ('' for nobody), 1 when the visitor said "not now" lately.
-- live:v:, live:claim: and live:dismissed: keys are built here: one Redis, not a cluster.
local ids = redis.call('zrangebyscore', KEYS[1], ARGV[1], '+inf', 'LIMIT', 0, tonumber(ARGV[2]))
local out = {}
for _, vid in ipairs(ids) do
  local visit = redis.call('hgetall', 'live:v:' .. vid)
  if #visit > 0 then
    out[#out + 1] = vid
    out[#out + 1] = visit
    out[#out + 1] = redis.call('get', 'live:claim:' .. vid) or ''
    out[#out + 1] = redis.call('exists', 'live:dismissed:' .. vid)
  end
end
return out
