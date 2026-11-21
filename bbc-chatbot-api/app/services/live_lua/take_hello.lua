-- take_hello.lua: the hello a tap answers, taken once (the API, POST /chat/init with live_tap).
-- KEYS[1] live:hello:<hello id>
local hello = redis.call('get', KEYS[1])
if hello then redis.call('del', KEYS[1]) end
return hello
