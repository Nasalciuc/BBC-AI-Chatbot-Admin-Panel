// "On the site now" in the widget (src/live.ts, contracts/live.md): what a beat carries (never what the visitor
// typed), how a hello is read (the contract's shape, plain text, not past its time) and the card that shows it.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { BEAT_PAGE_TYPES, beatBody, keptPass, nextPollDelay, readConfig, readHello, sourceOf } from '../src/liveRead.ts'

const here = dirname(fileURLToPath(import.meta.url))
const contract = JSON.parse(readFileSync(join(here, '..', '..', 'contracts', 'live_inbox_message.json'), 'utf8'))
const NOW = Date.parse('2026-11-21T22:05:00+00:00')

test('the message the API writes is read as a hello', () => {
  const h = readHello({ ok: true, messages: [contract] }, NOW)
  assert.deepEqual(h, {
    id: contract.id, agent: 'Ana', text: contract.text, tap: contract.tap, expiresAt: Date.parse(contract.expires_at),
  })
})

test('a hello past its time, of another kind, too long, or without a tap is no hello', () => {
  const bad = [
    { ...contract, expires_at: '2026-11-21T22:04:59+00:00' },
    { ...contract, kind: 'ad' },
    { ...contract, text: 'x'.repeat(301) },
    { ...contract, text: '   ' },
    { ...contract, tap: 'v1.not-a-tap' },
    { ...contract, id: '' },
    'a string',
    null,
  ]
  for (const m of bad) assert.equal(readHello({ messages: [m] }, NOW), null, JSON.stringify(m))
  assert.equal(readHello({ messages: 'nope' }, NOW), null)
  assert.equal(readHello(null, NOW), null)
})

test('a hello\u2019s length is counted as the API counts it: 300 emoji are 300 characters', () => {
  const emoji = '\u{1F600}'
  assert.ok(readHello({ messages: [{ ...contract, text: emoji.repeat(300) }] }, NOW), 'at the limit')
  assert.equal(readHello({ messages: [{ ...contract, text: emoji.repeat(301) }] }, NOW), null, 'over it')
})

test('Reply takes the turn, and a Reply that lost it changes nothing', () => {
  const src = readFileSync(join(here, '..', 'src', 'Widget.tsx'), 'utf8')
  const live = src.slice(src.indexOf('const openLive = async'), src.indexOf('// C2: the pill'))
  assert.match(live, /const run = \+\+noteRunRef\.current/)
  assert.match(live, /if \(cid && stillMine\(\)\)/)
  assert.match(live, /if \(!stillMine\(\)\) return\s+setTunnel\('sales'\)/)
})

test('the first good hello wins, and a long name is cut', () => {
  const h = readHello({ messages: [{ ...contract, kind: 'x' }, { ...contract, id: 'h2', agent: 'A'.repeat(80) }] }, NOW)
  assert.equal(h.id, 'h2')
  assert.equal(h.agent.length, 40)
})

const tokens = JSON.parse(readFileSync(join(here, '..', '..', 'contracts', 'live_tokens.json'), 'utf8'))
const PASS = tokens.pass.valid[0].value

test('the config: on only with a URL and a pass, numbers kept in range', () => {
  assert.deepEqual(readConfig({ enabled: false, url: 'https://live.example.test', pass: PASS }), { enabled: false })
  assert.deepEqual(readConfig({ enabled: true, pass: PASS }), { enabled: false })
  assert.deepEqual(readConfig({ enabled: true, url: 'javascript:alert(1)', pass: PASS }), { enabled: false })
  assert.deepEqual(readConfig({ enabled: true, url: 'https://live.example.test/' }), { enabled: false }, 'no pass, no beats')
  assert.deepEqual(readConfig({ enabled: true, url: 'https://live.example.test/', after_s: 20, beat_s: 9999, hello: true, pass: PASS }),
    { enabled: true, url: 'https://live.example.test', after_s: 20, beat_s: 15, hello: true, pass: PASS })
  assert.deepEqual(readConfig(null), { enabled: false })
})

test('a pass is the API\u2019s b1 shape, never the chat token or an agent token', () => {
  assert.equal(keptPass(PASS), PASS)
  for (const bad of [tokens.agent.valid[0].value, 'v1.3f1c2a7e-8b4d-4c1e-9a2f-6d5e4c3b2a10.1.2.' + 'a'.repeat(64),
    PASS.slice(0, -1), PASS + '0', 'b1.' + 'x'.repeat(401) + '.' + 'a'.repeat(64), 'b1..' + 'a'.repeat(64), '', null]) {
    assert.equal(keptPass(bad), null, String(bad))
    assert.deepEqual(readConfig({ enabled: true, url: 'https://live.example.test', pass: bad }), { enabled: false })
  }
})

test('the widget shows bbc-live its pass, never the chat token', () => {
  const src = readFileSync(join(here, '..', 'src', 'live.ts'), 'utf8')
  assert.match(src, /headers\.set\('X-Live-Pass', pass\)/)
  assert.doesNotMatch(src, /X-Visitor-Token|getVisitorToken/)
  assert.match(src, /\/api\/chat\/live`, \{\s*method: 'POST'/)
})

test('a beat carries the path, codes and dates, never the query', () => {
  const b = beatBody({
    path: '/business-class-flights?from=JFK&email=dana@example.com#x',
    trip: { source: 'kayak', origin_code: 'jfk', destination_code: 'IST', origin_name: 'New York', depart_date: '2026-12-14',
      return_date: '14 Dec', cabin: 'business', adults: 2, children: 1 },
    utm: { utm_source: 'kayak', utm_medium: 'cpc' }, phone: true, site: 'bbc', chatOpen: false, hasChat: false,
  })
  assert.deepEqual(b, {
    page: '/business-class-flights', from: 'JFK', to: 'IST', depart: '2026-12-14', cabin: 'business', pax: 3,
    source: 'kayak', device: 'phone', site: 'bbc', chat_open: false, has_chat: false,
  })
  assert.doesNotMatch(JSON.stringify(b), /dana|New York|14 Dec/)
})

test('a page without a trip beats the path alone', () => {
  const b = beatBody({ path: '', trip: null, utm: {}, phone: false, site: 'bct', chatOpen: true, hasChat: false })
  assert.deepEqual(b, { page: '/', source: 'site', device: 'computer', site: 'bct', chat_open: true, has_chat: false })
})

test('a beat says what the page is and what the visitor did there, for the agent who says hello (the owner, 22 Nov)', async () => {
  const { activityLine, PAGE_TYPES } = await import('../src/siteSignals.ts')
  const events = [
    { name: 'search_submitted', detail: { origin_code: 'JFK', destination_code: 'LHR', depart_date: '2026-12-14' }, at: 1 },
    { name: 'quote_form_opened', detail: {}, at: 2 },
    { name: 'quote_form_invalid', detail: { fields: ['phone'] }, at: 3 },
  ]
  const b = beatBody({ path: '/search', trip: null, utm: {}, phone: false, site: 'bbc', chatOpen: false, hasChat: false,
    pageType: 'search', activity: activityLine(events) })
  assert.equal(b.page_type, 'search')
  assert.equal(b.activity, 'search:JFK-LHR:2026-12-14;quote_opened;quote_invalid:phone')
  // Only a kind the site knows, and the line as the site's steps make it (bbc-live checks each step again).
  assert.equal(beatBody({ path: '/', trip: null, utm: {}, phone: false, site: 'bbc', chatOpen: false, hasChat: false,
    pageType: 'checkout', activity: 'x'.repeat(301) }).page_type, undefined)
  assert.deepEqual([...BEAT_PAGE_TYPES], [...PAGE_TYPES], 'the same list as the site signals')
  // The hook sends them on every beat.
  const hook = readFileSync(join(here, '..', 'src', 'live.ts'), 'utf8')
  assert.match(hook, /pageType: sitePage\(\)\?\.pageType \?\? null, activity: activityLine\(siteEvents\(\)\)/)
})

test('every step the widget can make passes bbc-live’s filter and the API’s, and a typed word does not', async () => {
  const { activityToken, readSiteEvent, FIELD_KINDS } = await import('../src/siteSignals.ts')
  // The pattern as bbc-live compiles it (internal/live/http.go activityStep): the Go raw strings, joined.
  const go = readFileSync(join(here, '..', '..', 'bbc-live', 'internal', 'live', 'http.go'), 'utf8')
  const start = go.indexOf('activityStep = regexp.MustCompile(')
  assert.ok(start >= 0, 'bbc-live still names its step pattern activityStep')
  const src = go.slice(start, go.indexOf('\n)', start))
  const step = new RegExp([...src.matchAll(/`([^`]*)`/g)].map((m) => m[1]).join(''))
  // The API's (app/security/metadata.py _ACTIVITY_TOKEN, read with fullmatch): its raw strings, the f-string's
  // _FIELD_KIND put in. The two services keep the same shapes: a step one keeps, the other keeps too.
  const py = readFileSync(join(here, '..', '..', 'bbc-chatbot-api', 'app', 'security', 'metadata.py'), 'utf8')
  const strings = (name) => {
    const at = py.indexOf(`\n${name} = `)
    assert.ok(at >= 0, `the API still names ${name}`)
    return [...py.slice(at, py.indexOf('\n)', at)).matchAll(/\b(rf|fr|r)"([^"]*)"/g)].map(([, p, s]) => ({ f: p.includes('f'), s }))
  }
  const kind = strings('_FIELD_KIND').map((x) => x.s).join('')
  const api = new RegExp('^(?:' + strings('_ACTIVITY_TOKEN').map(({ f, s }) =>
    (f ? s.replaceAll('{_FIELD_KIND}', kind).replaceAll('{{', '{').replaceAll('}}', '}') : s)).join('') + ')$')
  const ev = (name, detail = {}) => readSiteEvent(name, detail, 1_000, 10_000)
  const made = [
    ev('search_submitted', { origin_code: 'jfk', destination_code: 'LHR', depart_date: '2026-12-14' }),
    ev('search_submitted', { destination_code: 'IST' }), ev('search_submitted', { depart_date: '2026-12-14' }),
    ev('search_submitted', {}), ev('quote_form_opened'), ev('quote_form_step', { step: 1 }),
    ev('quote_form_step', { step: 20 }), ev('quote_form_step', {}), ev('quote_form_invalid', { fields: [...FIELD_KINDS] }),
    ev('quote_form_invalid', { fields: ['phone_number', 'departure date'] }), ev('quote_form_invalid', {}),
    ev('quote_form_submitting'), ev('quote_form_submitted'), ev('contact_clicked', {}),
    ...['server', 'network', 'captcha', 'other', 'teapot'].map((reason) => ev('quote_form_failed', { reason })),
    ...['phone', 'whatsapp', 'email', 'fax'].map((channel) => ev('contact_clicked', { channel })),
  ].filter(Boolean).map(activityToken)
  assert.equal(made.length, 23, 'every sample is a site event the widget keeps')
  for (const t of made) {
    assert.match(t, step, `bbc-live: ${t}`)
    assert.match(t, api, `the API: ${t}`)
  }
  const typed = ['search:dana@example.com', 'quote_invalid:call me', 'contact:sms', 'quote_step:555', 'search:jfk-lhr']
  for (const t of typed) assert.doesNotMatch(t, step, t)
  // The edges where two copies of a pattern drift: both answer the same.
  const edges = [...typed, 'search:?-?', 'search:?-IST', 'search:JFK-?:2026-12-14', 'search:2026-12-14', 'quote_step:0',
    'quote_step:20', 'quote_step:21', 'quote_step:05', `quote_invalid:${FIELD_KINDS.slice(0, 6).join('|')}`,
    `quote_invalid:${FIELD_KINDS.slice(0, 7).join('|')}`, 'quote_failed', 'quote_failed:teapot', 'contact', 'contact:fax']
  for (const e of edges) assert.equal(step.test(e), api.test(e), e)
})

test('an ad, as the moments read it (triggers.adVisit), is an ad here too', async () => {
  const { adVisit } = await import('../src/triggers.ts')
  const samples = [{ gclid: 'g' }, { msclkid: 'm' }, { utm_source: 'google', utm_medium: 'cpc' },
    { utm_source: 'bing', utm_medium: 'paid_search' }, { utm_source: 'kayak', utm_medium: 'cpc', gclid: 'g' }]
  for (const s of samples) {
    if (adVisit(s)) assert.equal(sourceOf(s), 'ads', JSON.stringify(s))
  }
})

test('where they came from, as the panel reads it', () => {
  assert.equal(sourceOf({ utm_source: 'Kayak', utm_medium: 'cpc' }), 'kayak')
  assert.equal(sourceOf({ kayak_click_id: 'k1' }), 'kayak')
  assert.equal(sourceOf({ gclid: 'g1' }), 'ads')
  assert.equal(sourceOf({ utm_source: 'bing', utm_medium: 'cpc' }), 'ads')
  assert.equal(sourceOf({ fbclid: 'f1' }), 'ads')
  assert.equal(sourceOf({ utm_source: 'facebook', utm_medium: 'paid_social' }), 'ads')
  assert.equal(sourceOf({ utm_source: 'newsletter', utm_medium: 'email' }), 'site')
  assert.equal(sourceOf({}), 'site')
})

test('the poll waits again at once after an answer, longer after failures', () => {
  assert.equal(nextPollDelay(204, 0), 0)
  assert.equal(nextPollDelay(200, 0), 0)
  assert.equal(nextPollDelay('error', 1), 4_000)
  assert.equal(nextPollDelay(502, 9), 30_000)
})

test('the card shows the hello as text, never as markup', () => {
  const src = readFileSync(join(here, '..', 'src', 'FloatingButtons.tsx'), 'utf8')
  const card = src.slice(src.indexOf('bw-nudge--hello'), src.indexOf(') : card && count === 0'))
  assert.match(card, /\{hello\.text\}/)
  assert.doesNotMatch(card, /dangerouslySetInnerHTML|innerHTML/)
})

test('a refused pass: a new one polls at once, a refusal stops, a renewal that failed for now waits', async () => {
  const { afterRefusedPass } = await import('../src/liveRead.ts')
  assert.equal(afterRefusedPass(true, 'later'), 'poll', 'the beat renewed it meanwhile')
  assert.equal(afterRefusedPass(false, 'ok'), 'poll')
  assert.equal(afterRefusedPass(false, 'off'), 'stop', 'no pass will come on this page')
  assert.equal(afterRefusedPass(false, 'later'), 'wait', 'offline once is not the end of the page (CodeRabbit #273)')
})

test('the first config is published only by the effect that is still running', () => {
  const src = readFileSync(join(here, '..', 'src', 'live.ts'), 'utf8')
  const start = src.slice(src.indexOf('const start = () => {'), src.indexOf('later(start, AFTER_S * 1000)'))
  // The guard that counts is the one inside the answer's callback, after the request (CodeRabbit #273, round 3).
  const answer = start.slice(start.indexOf('.then(cfg => {'), start.indexOf('.catch('))
  const guard = answer.indexOf('if (stopped) return')
  assert.ok(guard >= 0 && guard < answer.indexOf('configRef.current = cfg'),
    'a stale answer must not overwrite the running effect’s config')
})

test('no pass for now is kept apart from off: the API says later, or fails as busy or down (CodeRabbit #273)', async () => {
  const { failedAsk, renewalOf } = await import('../src/liveRead.ts')
  assert.deepEqual(readConfig({ enabled: false, later: true }), { enabled: false, later: true })
  assert.deepEqual(readConfig({ enabled: false }), { enabled: false })
  assert.deepEqual(readConfig({ enabled: true, url: 'https://live.example.test', pass: PASS, later: true }),
    { enabled: false, later: true }, 'later wins: no pass is used with it')
  for (const status of [500, 502, 503, 429]) assert.deepEqual(failedAsk(status), { enabled: false, later: true }, String(status))
  for (const status of [400, 401, 403, 404]) assert.deepEqual(failedAsk(status), { enabled: false }, String(status))
  assert.equal(renewalOf({ enabled: false, later: true }), 'later')
  assert.equal(renewalOf({ enabled: false }), 'off')
  assert.equal(renewalOf(readConfig({ enabled: true, url: 'https://live.example.test', pass: PASS })), 'ok')
  const { afterRefusedPass } = await import('../src/liveRead.ts')
  assert.equal(afterRefusedPass(false, renewalOf(failedAsk(503))), 'wait', 'the API down is no reason to end the page')
  assert.equal(afterRefusedPass(false, renewalOf({ enabled: false })), 'stop', 'switched off or refused still stops')
})

test('a renewal for now keeps the config in hand, and the first ask tries again later', () => {
  const src = readFileSync(join(here, '..', 'src', 'live.ts'), 'utf8')
  const renew = src.slice(src.indexOf('const renew = (): Promise<Renewal> => {'), src.indexOf('const beat = () => {'))
  assert.ok(renew.indexOf('if (cfg.later) return \'later\'') >= 0
    && renew.indexOf('if (cfg.later) return \'later\'') < renew.indexOf('configRef.current = cfg'),
    'the beats go on with the config in hand until a renewal brings a pass')
  const start = src.slice(src.indexOf('const start = () => {'), src.indexOf('later(start, AFTER_S * 1000)'))
  assert.match(start, /if \(cfg\.later\) \{ later\(start, RENEW_MS\); return \}/)
  assert.match(start, /\.catch\(\(\) => \{ if \(!stopped\) later\(start, RENEW_MS\) \}\)/)
  assert.match(src, /const cfg = res\.ok \? readConfig\(await res\.json\(\)\) : failedAsk\(res\.status\)/)
})

test('a pass is kept only for the visitor id it was asked for (CodeRabbit #273, round 4)', () => {
  const src = readFileSync(join(here, '..', 'src', 'live.ts'), 'utf8')
  const ask = src.slice(src.indexOf('async function askConfig('), src.indexOf('export function useLive('))
  assert.match(ask, /const visitorId = ensureVisitorId\(\)\s+const res = await apiFetch/)
  assert.match(ask, /visitor_id: visitorId,/)
  assert.match(ask, /if \(cfg\.enabled && cfg\.pass && ensureVisitorId\(\) === visitorId\) keepPass\(cfg\.pass\)/)
})
