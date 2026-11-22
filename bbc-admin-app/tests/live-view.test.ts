// "On the site now" in the panel (src/features/live/view.ts, contracts/live.md): who a visitor is to me, the
// trip in one line, the order of the list, and a hello held to the rules before the server holds it again.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  HELLO_RESTRICTED, claimProblem, heldBy, helloProblem, onSite, ordered, rowState, sourceLabel, suggestedHello, tripLine,
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
