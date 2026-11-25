-- bot_send.lua: Skye's own hello to a visitor on the site now (the API: live_bot.greet, from its run every few
-- seconds or POST /api/live/bot/hello). As atomic as an agent's (send.lua), without a claim before it: nobody may
-- hold the visitor, and the hello holds them as Skye for its life, so the panel says so and no agent claims them
-- in between. One hello a visitor while ARGV[5] runs, whoever sends it; one of Skye's while ARGV[6] runs.
-- KEYS[1] live:v:<vid>   KEYS[2] live:claim:<vid>   KEYS[3] live:dismissed:<vid>   KEYS[4] live:inbox:<vid>
-- KEYS[5] live:hello:<hello id>   KEYS[6] live:said:<vid>   KEYS[7] live:bot:<vid>
-- ARGV[1] Skye's id   ARGV[2] vid   ARGV[3] the message for the widget (JSON)   ARGV[4] the hello for /chat/init (JSON)
-- ARGV[5] the hello's life (seconds)   ARGV[6] how long Skye's one hello to a visitor lasts (seconds)
--  1 sent   -1 the visitor left   -2 in a chat   -3 said "not now"   -4 someone holds them
-- -5 a hello went already   -6 Skye said hello to them already
if redis.call('exists', KEYS[1]) == 0 then return -1 end
if redis.call('hget', KEYS[1], 'has_chat') == '1' then return -2 end
if redis.call('exists', KEYS[3]) == 1 then return -3 end
if redis.call('exists', KEYS[2]) == 1 then return -4 end
if redis.call('exists', KEYS[7]) == 1 then return -6 end
if not redis.call('set', KEYS[6], ARGV[1], 'NX', 'EX', tonumber(ARGV[5])) then return -5 end
redis.call('set', KEYS[7], '1', 'EX', tonumber(ARGV[6]))
redis.call('set', KEYS[2], ARGV[1], 'EX', tonumber(ARGV[5]))
redis.call('set', KEYS[5], ARGV[4], 'EX', tonumber(ARGV[5]))
redis.call('rpush', KEYS[4], ARGV[3])
redis.call('expire', KEYS[4], tonumber(ARGV[5]))
redis.call('publish', 'live:inbox', ARGV[2])
return 1
