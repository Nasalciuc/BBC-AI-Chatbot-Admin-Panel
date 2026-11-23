// "On the site now" in the panel (src/features/live/view.ts, contracts/live.md): who a visitor is to me, the
// trip in one line, the order of the list, and a hello held to the rules before the server holds it again.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  ALEX_GAVE_UP_S, HELLO_RESTRICTED, activityText, alexCardLine, alexCouldnt, alexLine, alexWaiting, claimProblem, heldBy,
  helloProblem, listed, onSite, ordered, pageLine, rowState, sourceLabel, stepText, suggestedHello, testLine, tripLine, clockS,
} from '../src/features/live/view.ts'

const ME = 'agent-me'
const v = (over = {}) => ({
  visitor_id: 'v-' + Math.random().toString(36).slice(2), first_at: 0, last_at: 0, seconds_on_site: 60, page: '/',
  source: 'site', chat_open: false, has_chat: false, dismissed: false, ...over,
})

test('who a visitor is to me', () => {
  assert.equal(rowState(v(), ME), 'free')
  assert.equal(rowState(v({ claimed_by: ME }), ME), 'mine')
  assert.equal(rowState(v({ claimed_by: 'someone' }), ME), 'taken')
  assert.equal(rowState(v({ dismissed: true, claimed_by: ME }), ME), 'declined')
  assert.equal(rowState(v({ has_chat: true, claimed_by: ME }), ME), 'in_chat')
})

test('mine first, then the ones nobody holds, the longest on the site first', () => {
  const rows = [
    v({ visitor_id: 'taken', claimed_by: 'x', seconds_on_site: 900 }),
    v({ visitor_id: 'free-short', seconds_on_site: 30 }),
    v({ visitor_id: 'mine', claimed_by: ME, seconds_on_site: 10 }),
    v({ visitor_id: 'free-long', seconds_on_site: 300 }),
    v({ visitor_id: 'chat', has_chat: true, seconds_on_site: 999 }),
  ]
  assert.deepEqual(ordered(rows, ME).map((r) => r.visitor_id), ['mine', 'free-long', 'free-short', 'taken', 'chat'])
  assert.equal(heldBy(rows, ME), 1)
  assert.equal(heldBy(rows, undefined), 0)
})

test('the trip in one line, or nothing', () => {
  assert.equal(tripLine(v({ trip: { from: 'JFK', to: 'IST', depart: '2026-12-14', return: '2027-01-02', cabin: 'business', pax: 2 } })),
    'JFK → IST · Dec 14 – Jan 2 · business · 2 travellers')
  assert.equal(tripLine(v({ trip: { to: 'LHR' } })), '? → LHR')
  assert.equal(tripLine(v({ trip: { depart: 'soon' } })), '')
  assert.equal(tripLine(v()), '')
})

test('time on the site and where they came from', () => {
  assert.equal(onSite(45), '45 s')
  assert.equal(onSite(185), '3 min')
  assert.equal(onSite(3900), '1 h 05')
  assert.equal(sourceLabel('kayak'), 'Kayak')
  assert.equal(sourceLabel('ads'), 'Ad')
  assert.equal(sourceLabel('weird'), 'Site')
})

test('a hello is words, not too many, and never a contact or a link', () => {
  assert.equal(helloProblem('Hi, I can check business class fares for you.'), null)
  assert.match(helloProblem('   '), /Write/)
  assert.match(helloProblem('x'.repeat(301)), /300/)
  assert.match(helloProblem('see www.example.com'), /link/)
  assert.match(helloProblem('write to ana@example.com'), /e-mail/)
  assert.match(helloProblem('call +1 214 208 5185'), /phone/)
})

test('a phone is a run of digits as the server reads it: dates and short numbers are words (CodeRabbit #273)', () => {
  // Commas break the digits into short runs: the server finds no contact and sends this one.
  assert.equal(helloProblem('I can check fares for Dec 14, 2026 and Jan 2, 2027.'), null)
  assert.equal(helloProblem('Your dates 2026-12-14 - 2027-01-02 look open.'), null)
  assert.equal(helloProblem('Leaving 14.12.2026, back 02.01.2027?'), null)
  assert.equal(helloProblem('2 adults, 1 child, 3 nights, gate 12 - 34 - 56.'), null)
  // A run the server takes for a phone, separators and all; a date that can't be one stays a phone.
  assert.match(helloProblem('reach me on 214.208.5185'), /phone/)
  assert.match(helloProblem('my number: (214) 208-5185'), /phone/)
  assert.match(helloProblem('code 12.34.5678'), /phone/)
  assert.match(helloProblem('2026-12-14 - 214 208 5185'), /phone/)
})

test('what the visitor did on the site, in words (the owner, 22 Nov: the agent knows what to say)', () => {
  assert.equal(stepText('search:JFK-LHR:2026-12-14'), 'Searched JFK → LHR, Dec 14')
  assert.equal(stepText('search:?-IST'), 'Searched ? → IST')
  assert.equal(stepText('search'), 'Searched flights')
  assert.equal(stepText('quote_opened'), 'Opened the quote form')
  assert.equal(stepText('quote_step:2'), 'Quote form, step 2')
  assert.equal(stepText('quote_invalid:phone|dates'), 'The quote form flagged phone, dates')
  assert.equal(stepText('quote_failed:network'), 'The quote form didn’t go through (connection)')
  assert.equal(stepText('quote_failed:other'), 'The quote form didn’t go through')
  assert.equal(stepText('quote_submitted'), 'Sent the quote form')
  assert.equal(stepText('contact:phone'), 'Clicked the phone number')
  assert.equal(stepText('teleport'), '', 'a step it does not know says nothing')
  const many = ['search:JFK-LHR', 'quote_opened', 'teleport', 'quote_step:1', 'quote_step:2', 'quote_invalid:email']
  assert.equal(activityText(v({ activity: many })),
    'Opened the quote form · Quote form, step 1 · Quote form, step 2 · The quote form flagged e-mail', 'the last four, oldest first')
  assert.equal(activityText(v()), '')
  assert.equal(pageLine(v({ page: '/search', page_type: 'search' })), 'Search results · /search')
  assert.equal(pageLine(v({ page: '/x', page_type: 'checkout' })), '/x', 'a kind it does not know: the path alone')
})

test('the first line meets what the visitor just did: a form that stuck or failed, a request sent', () => {
  assert.match(suggestedHello(v({ activity: ['quote_opened', 'quote_failed:server'], trip: { to: 'IST' } }), 'Ana'),
    /^Hi, I'm Ana from the travel team\. Did the quote form give you trouble\? I can take your request right here\.$/)
  assert.match(suggestedHello(v({ activity: ['quote_invalid:phone'] }), 'Ana'), /Need a hand with the quote form\?/)
  assert.match(suggestedHello(v({ activity: ['quote_submitted'] }), 'Ana'), /Thanks for your request\./)
  assert.match(suggestedHello(v({ page_type: 'thank_you' }), 'Ana'), /Thanks for your request\./)
  // Rule 5: steps that need no special words leave the line as the trip makes it.
  assert.match(suggestedHello(v({ activity: ['search:JFK-IST', 'quote_opened'], trip: { to: 'IST' } }), 'Ana'),
    /Looking at flights to IST\?/)
  assert.match(suggestedHello(v({ activity: ['teleport'] }), 'Ana'), /Can I help you find a business class fare\?$/)
})

test('the row shows the steps and the kind of page', () => {
  const page = readFileSync(new URL('../src/features/live/index.tsx', import.meta.url), 'utf8')
  assert.match(page, /\{activityText\(v\) && \(/)
  assert.match(page, /<span className='truncate text-xs text-k-6b6b6b'>\{pageLine\(v\)\}<\/span>/)
})

test('the first line names the place the page is about', () => {
  assert.match(suggestedHello(v({ trip: { to: 'IST' } }), 'Ana'), /^Hi, I'm Ana from the travel team\. Looking at flights to IST\?/)
  assert.doesNotMatch(suggestedHello(v(), ''), /undefined|null/)
})

test('a reader who may not say hello sees it locked with the reason, never missing (CodeRabbit #273)', () => {
  const page = readFileSync(new URL('../src/features/live/index.tsx', import.meta.url), 'utf8')
  assert.doesNotMatch(page, /\{pass\.can_say_hello && \(\s*<div/, 'the actions are not hidden for a reader who can’t act')
  assert.match(page, /state === 'free' && \(pass\.can_say_hello \? \(/)
  assert.match(page, /disabled title=\{HELLO_RESTRICTED\}/)
  assert.match(page, /<Lock aria-hidden='true'/)
  assert.match(HELLO_RESTRICTED, /sales line/)
})

test('a hello that lands closes its own editor, never another visitor’s (CodeRabbit #273)', () => {
  const page = readFileSync(new URL('../src/features/live/index.tsx', import.meta.url), 'utf8')
  const send = page.slice(page.indexOf('const send = useMutation'), page.indexOf('const problem = writing'))
  assert.match(send, /onSuccess: \(_res, \{ v \}\) =>/)
  assert.match(send, /setWriting\(\(cur\) => \(cur\?\.visitor_id === v\.visitor_id \? null : cur\)\)/)
  assert.doesNotMatch(send, /setWriting\(null\)/)
})

test('every refused claim says why in words', () => {
  for (const s of ['taken', 'full', 'declined', 'in_a_chat', 'gone']) assert.ok(claimProblem(s), s)
  assert.equal(claimProblem('claimed'), null)
  assert.equal(claimProblem('mine'), null)
})

test('the live pass is the session’s: cleared when it ends, never stored by a late beat (CodeRabbit #273)', () => {
  const read = (p: string) => readFileSync(new URL(`../${p}`, import.meta.url), 'utf8')
  const store = read('src/stores/live-store.ts')
  assert.match(store, /useAuthStore\.subscribe\(\(now, before\) => \{/)
  assert.match(store, /const ended = !now\.auth\.user && !now\.auth\.accessToken/)
  assert.match(store, /const otherAccount = \(now\.auth\.user\?\.accountNo \?\? ''\) !== \(before\.auth\.user\?\.accountNo \?\? ''\)/)
  assert.match(store, /if \(\(ended \|\| otherAccount\) && useLiveStore\.getState\(\)\.pass\) useLiveStore\.getState\(\)\.setFromBeat\(null\)/)
  // Sign-out, the 401 handler and an expired session all end it through the auth store's reset.
  for (const f of ['src/components/sign-out-dialog.tsx', 'src/main.tsx', 'src/routes/_authenticated/route.tsx']) {
    assert.match(read(f), /auth\.reset\(\)/, f)
  }
  const beat = read('src/hooks/use-heartbeat.ts')
  const process = beat.slice(beat.indexOf('const processResponse = (res: HeartbeatResponse) => {'),
    beat.indexOf('useLiveStore.getState().setFromBeat('))
  assert.match(process, /if \(!hasSession\(\)\) return/, 'a beat answered after the session ended stores nothing')
  const fallback = beat.slice(beat.indexOf("const res = await apiFetch<HeartbeatResponse>('/api/agent/heartbeat'"),
    beat.indexOf('// Heartbeat failure is non-fatal'))
  const guard = fallback.indexOf('if (!active.current) return')
  assert.ok(guard >= 0 && guard < fallback.indexOf('processResponse(res)'), 'the fallback drops an answer after cleanup')
})

test('the agent-first test: Alex’s countdown, the visitors he waits on first, the page’s line (the owner, 22 Nov)', () => {
  const now = 1_900_000_000
  assert.equal(alexLine(v({ alex_at: now + 80 }), now), 'Alex in 1:20')
  assert.equal(alexLine(v({ alex_at: now + 5 }), now), 'Alex in 0:05')
  assert.equal(alexLine(v({ alex_at: now }), now), '', 'his time came: no countdown')
  assert.equal(alexLine(v(), now), '')
  assert.equal(alexWaiting(v({ alex_at: now + 1 }), now), true)
  const rows = [
    v({ visitor_id: 'long', seconds_on_site: 900 }),
    v({ visitor_id: 'later', seconds_on_site: 30, alex_at: now + 100 }),
    v({ visitor_id: 'soon', seconds_on_site: 20, alex_at: now + 10 }),
    v({ visitor_id: 'past', seconds_on_site: 600, alex_at: now - 10 }),
    v({ visitor_id: 'mine', claimed_by: ME, seconds_on_site: 5 }),
  ]
  assert.deepEqual(ordered(rows, ME, now).map((r) => r.visitor_id), ['mine', 'soon', 'later', 'long', 'past'])
  // Rule 5: with nobody waited on, the order is as before.
  assert.deepEqual(ordered(rows.map((r) => ({ ...r, alex_at: undefined })), ME, now).map((r) => r.visitor_id),
    ['mine', 'long', 'past', 'later', 'soon'])
  assert.equal(testLine({ percent: 50, hold_s: 120 }),
    'Test running: 50% of visitors are listed here, the rest are Alex’s and aren’t. Alex waits 2 minutes for each before his card, while someone who may say hello has this page open: say hello before he does.')
  assert.match(testLine({ percent: 50, hold_s: 90 }), /Alex waits 1 minute 30 s for each/)
  assert.match(testLine({ percent: 50, hold_s: 0 }), /Alex doesn’t wait for them\.$/)
  for (const off of [undefined, null, { percent: 0, hold_s: 120 }]) assert.equal(testLine(off), '')
  const page = readFileSync(new URL('../src/features/live/index.tsx', import.meta.url), 'utf8')
  assert.match(page, /const nowS = clockS\(tickS, list\.dataUpdatedAt\)/)
  assert.match(page, /const rows = ordered\(listed\(list\.data\?\.data \?\? \[\], meId, nowS\), meId, nowS\)/)
  assert.match(page, /\{test && <p className='rounded-\[14px\] bg-k-fff3e0/)
  assert.match(page, /Test · \{alexLine\(v, nowS\)\}/)
  assert.match(page, /if \(!anyWaiting\) return\n\s+const t = setInterval\(\(\) => setTickS\(Math\.floor\(Date\.now\(\) \/ 1000\)\), 1000\)/)
})

test('the countdowns read the list’s last answer when the tick is older, the tick otherwise', () => {
  const now = 1_900_000_000
  // An hour with nobody waited on (no tick), then a row that waits: its countdown counts from the answer's second.
  assert.equal(clockS(now - 3600, (now + 2) * 1000 + 999), now + 2)
  assert.equal(alexLine(v({ alex_at: now + 80 }), clockS(now - 3600, now * 1000)), 'Alex in 1:20')
  // Rule 5: a tick after the answer is the newer second; no answer yet, the tick.
  assert.equal(clockS(now + 4, (now + 2) * 1000), now + 4)
  assert.equal(clockS(now, 0), now)
})

// ── The list is the visitors Alex's card didn't start a chat with (the owner, 23 Nov 2026) ─────────────────────

test('listed: who Alex’s card didn’t start a chat with, who Alex waits on, and the ones I hold', () => {
  const now = 1_900_000_000
  const rows = [
    v({ visitor_id: 'ignored', alex_card: 'shown', alex_card_s: 45 }),
    v({ visitor_id: 'not-now', alex_card: 'dismissed', alex_card_s: 5 }),
    v({ visitor_id: 'just-shown', alex_card: 'shown', alex_card_s: ALEX_GAVE_UP_S - 1 }),
    v({ visitor_id: 'answered', alex_card: 'engaged', alex_card_s: 300 }),
    v({ visitor_id: 'no-card', seconds_on_site: 900 }),
    v({ visitor_id: 'in-chat', alex_card: 'shown', alex_card_s: 300, has_chat: true }),
    v({ visitor_id: 'waited-on', alex_at: now + 60 }),
    v({ visitor_id: 'mine-now-in-chat', claimed_by: ME, has_chat: true, alex_card: 'engaged', alex_card_s: 90 }),
  ]
  assert.deepEqual(listed(rows, ME, now).map((r) => r.visitor_id), ['ignored', 'not-now', 'waited-on', 'mine-now-in-chat'])
  assert.equal(alexCouldnt(v({ alex_card: 'shown', alex_card_s: ALEX_GAVE_UP_S })), true, 'at the second')
  assert.equal(alexCouldnt(v({ alex_card: 'shown' })), false, 'no time, no verdict')
  // Rule 5: no card, nobody waited on, held by someone else or by nobody: not listed.
  assert.deepEqual(listed([v(), v({ claimed_by: 'someone-else', alex_card: 'engaged', alex_card_s: 99 })], ME, now), [])
  assert.deepEqual(listed([v({ claimed_by: 'x' })], undefined, now), [])
})

test('within a state, the one Alex’s card showed to last comes first', () => {
  const rows = [
    v({ visitor_id: 'old', alex_card: 'shown', alex_card_s: 600, seconds_on_site: 900 }),
    v({ visitor_id: 'fresh', alex_card: 'shown', alex_card_s: 31, seconds_on_site: 40 }),
    v({ visitor_id: 'not-now', alex_card: 'dismissed', alex_card_s: 120 }),
    v({ visitor_id: 'mine', claimed_by: ME, alex_card: 'shown', alex_card_s: 900 }),
  ]
  assert.deepEqual(ordered(rows, ME).map((r) => r.visitor_id), ['mine', 'fresh', 'not-now', 'old'])
})

test('what came of Alex’s card, in words on the row', () => {
  assert.equal(alexCardLine(v({ alex_card: 'shown', alex_card_s: 45, alex_trigger: 'kayak' })), 'Alex’s card 45 s ago, no answer')
  assert.equal(alexCardLine(v({ alex_card: 'shown', alex_card_s: 185, alex_trigger: 'note' })), 'Opened the chat 3 min ago, didn’t write')
  assert.equal(alexCardLine(v({ alex_card: 'dismissed', alex_card_s: 60, alex_trigger: 'ads' })), 'Said not now to Alex’s card')
  assert.equal(alexCardLine(v({ alex_card: 'dismissed', alex_trigger: 'note' })), 'Closed the chat with Alex')
  assert.equal(alexCardLine(v({ alex_card: 'engaged', alex_card_s: 10 })), 'Answered Alex')
  assert.equal(alexCardLine(v()), '')
  const page = readFileSync(new URL('../src/features/live/index.tsx', import.meta.url), 'utf8')
  assert.match(page, /\{alexCardLine\(v\) && \(/)
})
