-- bot_send.lua: the hello of an agent's own bot to a visitor on the site now (the API, POST /api/live/bot/hello,
-- app/services/live_bot.py). As atomic as an agent's (send.lua), without a claim before it, so without the visitors an
-- agent holds at a time (claim.lua ARGV[3]): nobody else may hold the visitor (the agent themself may, from the panel),
-- and the hello holds them as ARGV[1] for its life, so the panel says so and no agent claims them in between. One hello
-- a visitor while ARGV[5] runs, whoever sends it; one bot's hello a visitor while ARGV[6] runs, whoever's bot.
-- KEYS[1] live:v:<vid>   KEYS[2] live:claim:<vid>   KEYS[3] live:dismissed:<vid>   KEYS[4] live:inbox:<vid>
-- KEYS[5] live:hello:<hello id>   KEYS[6] live:said:<vid>   KEYS[7] live:bot:<vid>
-- ARGV[1] the holder, bot:<agent id>   ARGV[2] vid   ARGV[3] the message for the widget (JSON)
-- ARGV[4] the hello for /chat/init (JSON)   ARGV[5] the hello's life (seconds)
-- ARGV[6] how long a bot's one hello to a visitor lasts (seconds)   ARGV[7] the agent id
--  1 sent   -1 the visitor left   -2 in a chat   -3 said "not now"   -4 someone else holds them
-- -5 a hello went already   -6 a bot said hello to them already
if redis.call('exists', KEYS[1]) == 0 then return -1 end
if redis.call('hget', KEYS[1], 'has_chat') == '1' then return -2 end
if redis.call('exists', KEYS[3]) == 1 then return -3 end
local holder = redis.call('get', KEYS[2])
if holder and holder ~= ARGV[7] then return -4 end
if redis.call('exists', KEYS[7]) == 1 then return -6 end
if not redis.call('set', KEYS[6], ARGV[1], 'NX', 'EX', tonumber(ARGV[5])) then return -5 end
redis.call('set', KEYS[7], '1', 'EX', tonumber(ARGV[6]))
redis.call('set', KEYS[2], ARGV[1], 'EX', tonumber(ARGV[5]))
redis.call('set', KEYS[5], ARGV[4], 'EX', tonumber(ARGV[5]))
redis.call('rpush', KEYS[4], ARGV[3])
redis.call('expire', KEYS[4], tonumber(ARGV[5]))
redis.call('publish', 'live:inbox', ARGV[2])
return 1
