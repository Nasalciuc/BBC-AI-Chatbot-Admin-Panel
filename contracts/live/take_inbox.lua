-- take_inbox.lua: what waits for a visitor, taken once (bbc-live, GET /v1/inbox).
-- KEYS[1] live:inbox:<vid>
local msgs = redis.call('lrange', KEYS[1], 0, -1)
if #msgs > 0 then redis.call('del', KEYS[1]) end
return msgs
