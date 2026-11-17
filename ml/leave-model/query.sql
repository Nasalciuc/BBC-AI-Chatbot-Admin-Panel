-- Person-period table for the leave model (fit.py), from PostHog (HogQL, project BBC).
-- One row per cell: how many visitors were still on the site at the start of an interval (at_risk)
-- and how many of them left during it (left). Aggregated counts only: no ids, no personal data.
--
-- Window: 13 Nov 2026 14:00 → 16 Nov 2026 16:20 UTC — before Alex's cards existed (natural behaviour).
-- To refresh: run in PostHog's SQL editor (or the MCP execute-sql), export as CSV to person_period.csv.
WITH f AS (
  SELECT `$session_id` AS sid, min(timestamp) AS t0,
    argMin(coalesce(properties.utm_source, ''), timestamp) AS src,
    argMin(coalesce(properties.gclid, properties.msclkid, ''), timestamp) AS click_id,
    argMin(properties.$device_type, timestamp) AS dev,
    argMin(properties.$geoip_country_code, timestamp) AS cc,
    argMin(coalesce(properties.$webview_app, ''), timestamp) AS webview,
    max(if(properties.$virt_is_bot = true, 1, 0)) AS bot
  FROM events
  WHERE event = '$pageview' AND properties.$host = 'buybusinessclass.com'
    AND timestamp >= toDateTime('2026-11-13 14:00:00') AND timestamp < toDateTime('2026-11-16 16:20:00')
  GROUP BY sid
),
-- The visit's last signal: any event ($pageleave, $web_vitals, the widget's) or heatmap flush.
ev AS (
  SELECT `$session_id` AS sid, max(timestamp) AS ev_last,
    minIf(timestamp, event IN ('quote_form_opened', 'quote_form_step')) AS t_form
  FROM events
  WHERE timestamp >= toDateTime('2026-11-13 14:00:00') AND timestamp < toDateTime('2026-11-16 18:00:00')
  GROUP BY sid
),
-- Taps and pointer moves, as flushed (a flush comes after the touch: a feature at t never sees later).
hm AS (
  SELECT session_id AS sid, max(timestamp) AS hm_last,
    groupArrayIf(timestamp, type IN ('click', 'mousemove')) AS touches
  FROM heatmaps
  WHERE timestamp >= toDateTime('2026-11-13 14:00:00') AND timestamp < toDateTime('2026-11-16 18:00:00')
  GROUP BY sid
),
s AS (
  SELECT f.sid AS sid,
    multiIf(f.src = 'kayak', 'kayak', f.click_id != '' OR f.src IN ('google', 'google ', 'bing'), 'search_ad', 'other') AS source,
    if(f.dev = 'Mobile', 'phone', 'computer') AS device,
    if(f.webview != '', 1, 0) AS in_app,
    dateDiff('millisecond', f.t0, greatest(ev.ev_last, hm.hm_last)) / 1000 AS life,
    if(ev.t_form > f.t0, dateDiff('millisecond', f.t0, ev.t_form) / 1000, 1000000) AS form_s,
    arrayMap(x -> dateDiff('millisecond', f.t0, x) / 1000, hm.touches) AS touch_s,
    -- ~20% of the visits held out for the test, by session: no visitor in both halves.
    if(cityHash64(f.sid) % 5 = 0, 'test', 'train') AS split
  FROM f
  LEFT JOIN ev ON f.sid = ev.sid
  LEFT JOIN hm ON f.sid = hm.sid
  -- No bots, no visits from the team's countries.
  WHERE f.bot = 0 AND f.cc NOT IN ('MD', 'RO', 'IN', 'UA') AND f.dev IN ('Mobile', 'Desktop')
),
p AS (
  SELECT split, source, device, in_app, life,
    tupleElement(iv, 1) AS k, tupleElement(iv, 2) AS a, tupleElement(iv, 3) AS b,
    if(arrayExists(x -> x > 0 AND x <= tupleElement(iv, 2), touch_s), 1, 0) AS touched,
    if(form_s <= tupleElement(iv, 2), 1, 0) AS form
  FROM s
  ARRAY JOIN [(0, 0, 2), (1, 2, 5), (2, 5, 10), (3, 10, 15), (4, 15, 20), (5, 20, 30), (6, 30, 45), (7, 45, 60), (8, 60, 90), (9, 90, 120)] AS iv
  WHERE life >= tupleElement(iv, 2)
)
SELECT split, k, source, device, in_app, touched, form, count() AS at_risk, countIf(life < b) AS left
FROM p
GROUP BY split, k, source, device, in_app, touched, form
ORDER BY split, k, source, device, in_app, touched, form
-- Above the editor's default of 100 rows: the whole table is a few hundred cells.
LIMIT 5000
