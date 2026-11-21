-- send.lua: an agent's hello to a visitor they hold (the API, POST /api/live/send). One hello a visitor
-- while ARGV[5] runs, whoever sends it.
-- KEYS[1] live:claim:<vid>   KEYS[2] live:dismissed:<vid>   KEYS[3] live:inbox:<vid>
-- KEYS[4] live:hello:<hello id>   KEYS[5] live:said:<vid>
-- ARGV[1] agent id   ARGV[2] vid   ARGV[3] the message for the widget (JSON)
-- ARGV[4] the hello for /chat/init (JSON)   ARGV[5] seconds
--  1 sent   -1 not this agent's visitor   -2 the visitor said "not now"   -3 a hello went already
if redis.call('get', KEYS[1]) ~= ARGV[1] then return -1 end
if redis.call('exists', KEYS[2]) == 1 then return -2 end
if not redis.call('set', KEYS[5], ARGV[1], 'NX', 'EX', tonumber(ARGV[5])) then return -3 end
redis.call('set', KEYS[4], ARGV[4], 'EX', tonumber(ARGV[5]))
redis.call('rpush', KEYS[3], ARGV[3])
redis.call('expire', KEYS[3], tonumber(ARGV[5]))
redis.call('publish', 'live:inbox', ARGV[2])
return 1
