// "On the site now" in the panel (src/features/live/view.ts, contracts/live.md): who a visitor is to me, the
// trip in one line, the order of the list, and a hello held to the rules before the server holds it again.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  ALEX_GAVE_UP_S, HELLO_RESTRICTED, activityText, alexCardLine, alexCouldnt, alexLine, alexWaiting, claimProblem, heldBy,
  helloProblem, listed, onSite, onSiteSeconds, ordered, pageLine, rowState, sourceLabel, stepText, suggestedHello, testLine,
  tripLine, clockS, GROUP_LABEL, anzZone, daysToFlight, groupHello, groupOf, helloDraft, stuckInForm,
  FORM_HOLD, FORM_QUIET_S, fillingForm, stepClock, ROW_LABEL, keyDay, BOT_CARD_SHOWN,
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
  // An agent's own bot holds the visitor while its hello is out (bbc-chatbot-api live_bot.py): mine or a colleague's,
  // never a button, and never one of the three I hold.
  assert.equal(rowState(v({ claimed_by: `bot:${ME}` }), ME), 'my_bot')
  assert.equal(rowState(v({ claimed_by: 'bot:someone' }), ME), 'bot')
  assert.equal(rowState(v({ claimed_by: `bot:${ME}` }), undefined), 'bot')
  assert.equal(ROW_LABEL.my_bot, 'Your bot said hello')
  assert.equal(rowState(v({ claimed_by: `bot:${ME}`, dismissed: true }), ME), 'declined')
  assert.equal(rowState(v({ claimed_by: `bot:${ME}`, has_chat: true }), ME), 'in_chat')
  assert.equal(heldBy([v({ claimed_by: `bot:${ME}` }), v({ claimed_by: ME })], ME), 1)
  assert.deepEqual(listed([v({ visitor_id: 'mine-by-bot', claimed_by: `bot:${ME}` })], ME, 0).map((r) => r.visitor_id),
    ['mine-by-bot'])
  const page = readFileSync(new URL('../src/features/live/index.tsx', import.meta.url), 'utf8')
  assert.match(page, /my_bot: 'bg-k-e3f6ea text-k-0a6b35',/)
  assert.doesNotMatch(page, /state === '(my_)?bot'/, 'no say-hello button on a visitor a bot greeted')
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
  assert.equal(alexLine(v({ alex_at: now + 80 }), now), 'Skye in 1:20')
  assert.equal(alexLine(v({ alex_at: now + 5 }), now), 'Skye in 0:05')
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
    'Test running: 50% of visitors are listed here, the rest are Skye’s and aren’t. Skye waits 2 minutes for each before his card, while someone who may say hello has this page open: say hello before he does.')
  assert.match(testLine({ percent: 50, hold_s: 90 }), /Skye waits 1 minute 30 s for each/)
  assert.match(testLine({ percent: 50, hold_s: 0 }), /Skye doesn’t wait for them\.$/)
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
  assert.equal(alexLine(v({ alex_at: now + 80 }), clockS(now - 3600, now * 1000)), 'Skye in 1:20')
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
  // A visitor Alex waits on who opened the chat meanwhile is in it, not waiting (CodeRabbit #278).
  assert.deepEqual(listed([v({ alex_at: now + 60, has_chat: true })], ME, now), [])
  assert.equal(listed([v({ alex_at: now + 60, has_chat: true, claimed_by: ME })], ME, now).length, 1, 'mine stays')
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
  assert.equal(alexCardLine(v({ alex_card: 'shown', alex_card_s: 45, alex_trigger: 'kayak' })), 'Skye’s card 45 s ago, no answer')
  assert.equal(alexCardLine(v({ alex_card: 'shown', alex_card_s: 185, alex_trigger: 'note' })), 'Opened the chat 3 min ago, didn’t write')
  assert.equal(alexCardLine(v({ alex_card: 'dismissed', alex_card_s: 60, alex_trigger: 'ads' })), 'Said not now to Skye’s card')
  assert.equal(alexCardLine(v({ alex_card: 'dismissed', alex_trigger: 'note' })), 'Closed the chat with Skye')
  assert.equal(alexCardLine(v({ alex_card: 'engaged', alex_card_s: 10 })), 'Answered Skye')
  assert.equal(alexCardLine(v()), '')
  const page = readFileSync(new URL('../src/features/live/index.tsx', import.meta.url), 'utf8')
  assert.match(page, /\{alexCardLine\(v\) && \(/)
})

test('time on the site is never less than the time since Alex’s card (the first beat comes at 20 s)', () => {
  assert.equal(onSiteSeconds(v({ seconds_on_site: 15, alex_card: 'shown', alex_card_s: 32 })), 32)
  assert.equal(onSiteSeconds(v({ seconds_on_site: 300, alex_card: 'shown', alex_card_s: 32 })), 300)
  assert.equal(onSiteSeconds(v({ seconds_on_site: 45 })), 45)
  const page = readFileSync(new URL('../src/features/live/index.tsx', import.meta.url), 'utf8')
  assert.match(page, /\{onSite\(onSiteSeconds\(v\)\)\} on the site/)
})

// ── The groups (the owner, 25 Nov 2026): a label and a hello ready, sent in one tap ─────────────────────────

const NOW_MS = Date.parse('2026-11-25T12:00:00Z')

test('a group by the time zone, the days to the flight, the quote form and a return', () => {
  assert.equal(anzZone('Australia/Sydney'), true)
  assert.equal(anzZone('Australia/Perth'), true)
  assert.equal(anzZone('Pacific/Auckland'), true)
  assert.equal(anzZone('Pacific/Chatham'), true)
  for (const tz of [undefined, '', 'Pacific/Fiji', 'Asia/Singapore', 'America/New_York', 'australia/sydney']) {
    assert.equal(anzZone(tz), false, String(tz))
  }
  assert.equal(daysToFlight('2026-12-10', NOW_MS), 15)
  assert.equal(daysToFlight('2027-01-09', NOW_MS), 45)
  assert.equal(daysToFlight('2026-11-25', NOW_MS), 0)
  assert.equal(daysToFlight('soon', NOW_MS), null)
  assert.equal(daysToFlight(undefined, NOW_MS), null)
  assert.equal(stuckInForm({ activity: ['search:JFK-LHR', 'quote_opened'] }), true)
  assert.equal(stuckInForm({ activity: ['quote_opened', 'quote_step:2'] }), true)
  assert.equal(stuckInForm({ activity: ['quote_opened', 'quote_invalid:phone'] }), true)
  assert.equal(stuckInForm({ activity: ['quote_opened', 'quote_failed:server'] }), true)
  // Rule 5: sent, being sent, never opened, or opened and sent since.
  assert.equal(stuckInForm({ activity: ['quote_opened', 'quote_submitted'] }), false)
  assert.equal(stuckInForm({ activity: ['quote_opened', 'quote_submitting'] }), false)
  assert.equal(stuckInForm({ activity: ['search:JFK-LHR'] }), false)
  assert.equal(stuckInForm({}), false)
})

test('the first group that fits; on a phone only the form and a return count', () => {
  const computer = { device: 'computer' }
  assert.equal(groupOf(v({ ...computer, activity: ['quote_opened'], tz: 'Australia/Sydney', returning: true }), NOW_MS), 'form')
  assert.equal(groupOf(v({ ...computer, tz: 'Australia/Sydney', returning: true, trip: { depart: '2026-12-20' } }), NOW_MS), 'anz')
  assert.equal(groupOf(v({ ...computer, trip: { to: 'IST', depart: '2026-12-20' }, returning: true }), NOW_MS), 'soon')
  assert.equal(groupOf(v({ ...computer, returning: true }), NOW_MS), 'back')
  // Rule 5: the same visitors on a phone, out of the window, or new.
  assert.equal(groupOf(v({ device: 'phone', tz: 'Australia/Sydney' }), NOW_MS), null)
  assert.equal(groupOf(v({ device: 'phone', trip: { depart: '2026-12-20' } }), NOW_MS), null)
  assert.equal(groupOf(v({ device: 'phone', activity: ['quote_invalid:email'] }), NOW_MS), 'form')
  assert.equal(groupOf(v({ device: 'phone', returning: true }), NOW_MS), 'back')
  assert.equal(groupOf(v({ ...computer, trip: { depart: '2026-12-09' } }), NOW_MS), null, '14 days')
  assert.equal(groupOf(v({ ...computer, trip: { depart: '2027-01-10' } }), NOW_MS), null, '46 days')
  assert.equal(groupOf(v({ ...computer, tz: 'Europe/London' }), NOW_MS), null)
  assert.equal(groupOf(v(), NOW_MS), null)
  assert.deepEqual(Object.keys(GROUP_LABEL), ['form', 'anz', 'soon', 'back'])
})

test('each ready hello passes the hello’s rules and the client’s: no price, no contact, no long dash', () => {
  const cases = [
    ['form', v({ activity: ['quote_opened', 'quote_invalid:phone'] })],
    ['form', v({ activity: ['quote_opened', 'quote_failed:server'] })],
    ['anz', v({ device: 'computer', tz: 'Australia/Sydney', trip: { to: 'LHR' } })],
    ['anz', v({ device: 'computer', tz: 'Pacific/Auckland' })],
    ['soon', v({ device: 'computer', trip: { to: 'IST', depart: '2026-12-20' } })],
    ['soon', v({ device: 'computer', trip: { depart: '2026-12-20' } })],
    ['back', v({ returning: true, trip: { to: 'CDG' } })],
    ['back', v({ returning: true })],
  ]
  for (const [group, visitor] of cases) {
    for (const name of ['Ana', '']) {
      const words = groupHello(group, visitor, name)
      assert.equal(helloProblem(words), null, words)
      assert.doesNotMatch(words, /—|\$|€|£|\bprice|\bcheap/i, words)
      assert.ok(words.startsWith(name ? 'Hi, I’m Ana'.replace('’', "'") : 'Hi, I am from the travel team.'), words)
    }
  }
  assert.equal(groupHello('form', v({ activity: ['quote_failed:server'] }), 'Ana'),
    "Hi, I'm Ana from the travel team. Did the quote form give you trouble? I can take your request right here.")
  assert.equal(groupHello('soon', v({ trip: { to: 'IST', depart: '2026-12-20' } }), 'Ana'),
    "Hi, I'm Ana from the travel team. Flying to IST on Dec 20? A good moment to book business class. Want me to check options for your dates?")
  assert.equal(groupHello('back', v({ returning: true }), 'Ana'),
    "Hi, I'm Ana from the travel team. Welcome back! Want me to pick up your search and check business class options for you?")
})

test('the row: the label, the hello quoted, one tap to send it, Write to change it first', () => {
  const page = readFileSync(new URL('../src/features/live/index.tsx', import.meta.url), 'utf-8')
  assert.match(page, /\{group && \(\s*<span className='rounded-full bg-k-efeaff px-2 py-0\.5 text-xs font-medium text-k-4a36c4'>\{GROUP_LABEL\[group\]\}<\/span>/)
  assert.match(page, /Ready: “\{ready\}”/)
  // One tap: taken when nobody holds them, then the hello as it is, with its group.
  assert.match(page, /if \(rowState\(v, meId\) !== 'mine'\) \{\s*const problem = claimProblem\(\(await claimVisitor\(pass!, v\.visitor_id\)\)\.state\)\s*if \(problem\) throw new Error\(problem\)\s*\}\s*return sendHello\(v\.visitor_id, words, group\)/)
  assert.equal((page.match(/onClick=\{\(\) => quick\.mutate\(\{ v, words: ready, group \}\)\}>\s*Send this hello/g) ?? []).length, 2,
    'for a visitor nobody holds, and for one I hold')
  // The editor starts from the group's hello too, and sends with the group its draft was written for.
  assert.match(page, /const draft = helloDraft\(v, nowMs, firstName\)\s*setText\(draft\.words\)\s*setWritingGroup\(draft\.group\)/)
  assert.match(page, /mutationFn: \(\{ v, words, group \}: \{ v: LiveVisitor; words: string; group: HelloGroup \| null \}\) =>\s*sendHello\(v\.visitor_id, words, group\)/)
  assert.match(page, /send\.mutate\(\{ v: writing, words: text\.trim\(\), group: writingGroup \}\)/)
  assert.doesNotMatch(page, /sendHello\([^)]*groupOf\(/, 'the group is never worked out again when the hello is sent')
  const api = readFileSync(new URL('../src/lib/api.ts', import.meta.url), 'utf-8')
  assert.match(api, /body: JSON\.stringify\(\{ visitor_id: visitorId, text, \.\.\.\(group \? \{ group \} : \{\}\) \}\)/)
})

test('the editor’s draft keeps the group it was written for across midnight UTC (CodeRabbit #282, round 3)', () => {
  // A returning visitor on a computer, flying on 10 Dec: 15 days away until midnight, 14 after it.
  const visitor = v({ device: 'computer', returning: true, trip: { to: 'IST', depart: '2026-12-10' } })
  const before = Date.parse('2026-11-25T23:59:30Z')
  const after = Date.parse('2026-11-26T00:00:30Z')
  const draft = helloDraft(visitor, before, 'Ana')
  assert.equal(draft.group, 'soon')
  assert.equal(draft.words, groupHello('soon', visitor, 'Ana'))
  assert.equal(groupOf(visitor, after), 'back', 'worked out again after midnight, the group would no longer be the draft’s')
  // No group: the page's hello, and no group to send.
  const plain = helloDraft(v({ device: 'phone', trip: { to: 'IST', depart: '2026-12-10' } }), before, 'Ana')
  assert.equal(plain.group, null)
  assert.equal(plain.words, suggestedHello(v({ device: 'phone', trip: { to: 'IST', depart: '2026-12-10' } }), 'Ana'))
})

test('a visitor filling in the quote form gets no hello until they stop for two minutes (25 Nov)', () => {
  // On 24–25 Nov 4 hellos reached visitors filling in the form: 3 sent it 5–37 s later.
  const T0 = Date.parse('2026-11-25T18:00:00Z')
  const filling = v({ visitor_id: 'v-form', activity: ['search:JFK-LHR', 'quote_opened', 'quote_step:2'] })
  let seen = stepClock(new Map(), [filling], T0)
  assert.equal(FORM_QUIET_S, 120)
  assert.equal(fillingForm(filling, seen, T0), true)
  assert.equal(fillingForm(filling, seen, T0 + 119_000), true)
  // Two minutes without a new step: stuck, as before, and the group's hello is back.
  assert.equal(fillingForm(filling, seen, T0 + 120_000), false)
  assert.equal(groupOf(filling, T0 + 120_000), 'form')
  // A new step starts the clock again; the same steps in a later list keep their time.
  const moved = { ...filling, activity: [...filling.activity, 'quote_step:3'] }
  seen = stepClock(seen, [moved], T0 + 100_000)
  assert.equal(fillingForm(moved, seen, T0 + 200_000), true)
  assert.equal(stepClock(seen, [moved], T0 + 150_000).get('v-form')?.at, T0 + 100_000)
  // Steps the clock hasn't seen yet just came, and a visitor first seen counts as just moved.
  assert.equal(fillingForm(moved, new Map(), T0), true)
  // A visitor no longer listed is forgotten.
  assert.equal(stepClock(seen, [], T0).size, 0)
  // Rule 5, the visitors it is not for: the newest step is not the form opened or moved on.
  for (const activity of [
    ['quote_opened', 'quote_invalid:phone'], ['quote_opened', 'quote_failed:server'], ['quote_opened', 'quote_submitting'],
    ['quote_opened', 'quote_submitted'], ['quote_opened', 'search:JFK-LHR'], ['search:JFK-LHR'], [],
  ]) {
    const other = v({ activity })
    assert.equal(fillingForm(other, stepClock(new Map(), [other], T0), T0), false, activity.join(' '))
  }
  assert.equal(fillingForm(v(), new Map(), T0), false)
  assert.doesNotMatch(FORM_HOLD, /—/)
})

test('the row holds the hello while they fill in the form, with the reason; the editor too', () => {
  const page = readFileSync(new URL('../src/features/live/index.tsx', import.meta.url), 'utf8')
  // The clock moves on with each list, while rendering (no state set in an effect).
  assert.match(page, /if \(list\.data && clock\.at !== list\.dataUpdatedAt\) \{\s+setClock\(\{ at: list\.dataUpdatedAt, seen: stepClock\(clock\.seen, list\.data\.data \?\? \[\], list\.dataUpdatedAt\) \}\)\s+\}/)
  // No group's hello ready while filling, and the row says why.
  assert.match(page, /const filling = fillingForm\(v, seen, nowMs\)\s+const group = filling \? null : groupOf\(v, nowMs\)/)
  assert.match(page, /\{filling && \(\s+<span title=\{FORM_HOLD\}[^>]*>\s+Filling in the quote form now/)
  // Say hello (nobody holds them) and Write (I hold them) wait, with the reason; Let go stays.
  assert.match(page, /title=\{filling \? FORM_HOLD : undefined\}\s+disabled=\{busy \|\| held >= maxClaims \|\| filling\} onClick=\{\(\) => claim\.mutate\(v\)\}>\s+Say hello/)
  assert.match(page, /title=\{filling \? FORM_HOLD : undefined\} disabled=\{filling\} onClick=\{\(\) => openEditor\(v\)\}>\s+Write/)
  // The editor's visitor as the list shows them now: one who started on the form since it opened waits too.
  assert.match(page, /const problem = writingNow \? \(fillingForm\(writingNow, seen, nowMs\) \? FORM_HOLD : helloProblem\(text\)\) : null/)
})

test('the bot card: the key shown once, a new one or Revoke, and what the bot calls (the owner, 25 Nov)', () => {
  const card = readFileSync(new URL('../src/features/live/components/bot-key-card.tsx', import.meta.url), 'utf8')
  assert.match(card, /The key, shown this once/)
  assert.match(card, /New key \(stops the old one\)/)
  assert.match(card, /revokeLiveBotKey/)
  // CodeRabbit #282, round 7: the key made is the mutation's answer (no copy in state), cleared by Revoke, and neither
  // button runs while the other's call is out (a new key answering after a revoke would show a stopped key).
  assert.doesNotMatch(card, /useState/)
  assert.match(card, /const made = create\.data/)
  assert.match(card, /onSuccess: \(\) => \{\s+create\.reset\(\)/)
  assert.match(card, /const busy = create\.isPending \|\| revoke\.isPending/)
  assert.match(card, /disabled=\{busy \|\| status\.isLoading\}\s+onClick=\{\(\) => create\.mutate\(\)\}/)
  assert.match(card, /disabled=\{busy\} onClick=\{\(\) => revoke\.mutate\(\)\}/)
  for (const call of ['/visitors', '/hello', '/chats', '/chats/&lt;id&gt;/messages']) assert.ok(card.includes(call), call)
  const page = readFileSync(new URL('../src/features/live/index.tsx', import.meta.url), 'utf8')
  assert.match(page, /\{BOT_CARD_SHOWN && pass\?\.can_say_hello && <BotKeyCard \/>\}/)
  const api = readFileSync(new URL('../src/lib/api.ts', import.meta.url), 'utf8')
  assert.match(api, /'\/api\/live\/bot-key', \{ method: 'POST' \}/)
  assert.match(api, /'\/api\/live\/bot-key', \{ method: 'DELETE' \}/)
  assert.equal(keyDay('2027-02-23T18:00:00+00:00'), 'Feb 23, 2027')
  assert.equal(keyDay(null), '')
  assert.equal(keyDay('not a day'), '')
})

test('the bot card is hidden for now, even from an agent who may say hello (the owner, 26 Nov)', () => {
  // Hidden: the switch is off, and the page shows the card only behind it.
  assert.equal(BOT_CARD_SHOWN, false)
  const page = readFileSync(new URL('../src/features/live/index.tsx', import.meta.url), 'utf8')
  assert.equal(page.split('<BotKeyCard').length - 1, 1, 'the card appears in one place only')
  assert.match(page, /\{BOT_CARD_SHOWN && pass\?\.can_say_hello && <BotKeyCard \/>\}/)
  // Rule 5: switched on again, a reader who may not say hello still doesn't get it (the second condition stays).
  assert.doesNotMatch(page, /\{BOT_CARD_SHOWN && <BotKeyCard \/>\}/)
})
