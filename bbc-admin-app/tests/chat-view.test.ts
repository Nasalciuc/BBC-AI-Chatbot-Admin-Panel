// What the Conversations screens say — pinned on the pure helpers in
// features/chats/view.ts. Runs with: node --experimental-strip-types --test tests/chat-view.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { Conversation, Lead } from '../src/lib/types.ts'
import {
  aboutOf,
  countdown,
  crmView,
  deadlineLeft,
  firstName,
  fullDate,
  initials,
  levelOf,
  minutesLabel,
  missingForReady,
  noSummaryText,
  notesOf,
  ownerOf,
  presenceView,
  previewText,
  queueLine,
  routeLine,
  takenBlind,
  seconds,
  signalOf,
  travelersText,
  tripRows,
  tripText,
  whereRows,
} from '../src/features/chats/view.ts'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p: string) => readFileSync(join(root, p), 'utf-8')

const ME = 'me-1'
const NOW = Date.parse('2026-11-06T12:00:00Z')
const iso = (msAgo: number) => new Date(NOW - msAgo).toISOString()

function conv(over: Partial<Conversation> = {}): Conversation {
  return {
    id: 'c1', tunnel: 'sales', mode: 'human', status: 'active', visitor_name: 'Michael Turner',
    visitor_email: null, visitor_phone: null, assigned_agent_id: ME, message_count: 4, ai_cost_total: 0,
    created_at: iso(600_000), updated_at: iso(60_000), closed_at: null, metadata: {}, ...over,
  } as Conversation
}

function lead(over: Partial<Lead> = {}): Lead {
  return {
    id: 'L1', conversation_id: 'c1', trip_type: 'round_trip', cabin_class: 'business', passengers: 2,
    flexible_dates: false, origin_code: 'JFK', destination_code: 'LHR', departure_date: '2026-11-16',
    return_date: '2026-11-23', route_display: 'New York → London', score: 60, tier: 'silver', status: 'new',
    intent_signals: {}, notes: '', created_at: iso(0), updated_at: iso(0), contacted_at: null, converted_at: null,
    created_in_crm: false, crm_lead_id: null, visitor_name: 'Michael Turner', visitor_email: 'm@x.com',
    visitor_phone: '+1 555 0110', ...over,
  } as Lead
}

test('names and numbers read like people write them', () => {
  assert.equal(initials('Michael Turner'), 'MT')
  assert.equal(initials('Ana'), 'AN')
  assert.equal(initials(null), '?')
  assert.equal(firstName('Sarah Collins'), 'Sarah')
  assert.equal(firstName(''), 'The client')
  assert.equal(countdown(70), '1:10')
  assert.equal(countdown(-4), '0:00')
  assert.equal(minutesLabel(0), 'just now')
  assert.equal(minutesLabel(32), '32 min')
  assert.equal(minutesLabel(65), '1 h 5 min')
  assert.equal(seconds(41), '41 s')
  assert.equal(seconds(null), '—')
})

test('presence says what the server derived, and nothing it did not', () => {
  assert.equal(presenceView('online', 5).label, 'On the site now')
  assert.equal(presenceView('minimized', 60).label, 'On the site · chat window closed')
  // A fresh "left" is usually a page change, not a client who is gone.
  assert.equal(presenceView('left', 30).label, 'Left just now')
  assert.equal(presenceView('left', 180).label, 'Left 3 min ago')
  assert.equal(presenceView('stale', 600).label, 'No signal for 10 min')
  assert.equal(presenceView(undefined, null).label, '', 'no signal at all: no claim')
})

test('whose chat: assigned is assigned, whatever the mode', () => {
  assert.equal(ownerOf(conv(), ME), 'mine')
  // A silent reservation (needs_agent, assigned to me) is mine to answer.
  assert.equal(ownerOf(conv({ status: 'needs_agent', mode: 'ai' }), ME), 'mine')
  assert.equal(ownerOf(conv({ assigned_agent_id: 'someone' }), ME), 'agent')
  assert.equal(ownerOf(conv({ assigned_agent_id: null, status: 'needs_agent', mode: 'ai' }), ME), 'waiting')
  assert.equal(ownerOf(conv({ assigned_agent_id: null, mode: 'ai' }), ME), 'ai')
  assert.equal(ownerOf(conv({ status: 'closed' }), ME), 'closed')
})

test('the countdown after Take is the server deadline, and stops at the first reply', () => {
  const taken = conv({ metadata: { agent_assigned_at: iso(20_000) }, last_agent_message_at: null })
  assert.equal(deadlineLeft(taken, NOW, 90), 70)
  assert.equal(deadlineLeft(taken, NOW, 120), 100)
  // Past zero, "0:00" until the 15-second sweep acts.
  assert.equal(deadlineLeft(conv({ metadata: { agent_assigned_at: iso(200_000) } }), NOW, 90), 0)
  // Answered after the take: no countdown.
  assert.equal(deadlineLeft(conv({ metadata: { agent_assigned_at: iso(60_000) }, last_agent_message_at: iso(30_000) }), NOW, 90), null)
  // An answer from BEFORE the take does not count.
  assert.equal(deadlineLeft(conv({ metadata: { agent_assigned_at: iso(20_000) }, last_agent_message_at: iso(600_000) }), NOW, 90), 70)
})

test('row signals: just taken, no reply (red from 5 min), your turn, closed', () => {
  const justTaken = conv({ metadata: { agent_assigned_at: iso(20_000) }, last_user_message_at: iso(25_000) })
  assert.equal(signalOf(justTaken, 'mine', NOW, 90)?.text, 'Just taken · reply within 1:10')
  const red = conv({ last_user_message_at: iso(32 * 60_000), last_agent_message_at: iso(40 * 60_000) })
  assert.deepEqual(signalOf(red, 'mine', NOW, 90), { text: 'No reply 32 min', urgency: 0, clock: true })
  const amber = conv({ last_user_message_at: iso(60_000), last_agent_message_at: iso(120_000) })
  assert.equal(signalOf(amber, 'mine', NOW, 90)?.text, 'Your turn · 1 min')
  // Someone else's chat: only red is the viewer's business.
  assert.equal(signalOf(amber, 'agent', NOW, 90), null)
  const answered = conv({ last_user_message_at: iso(120_000), last_agent_message_at: iso(60_000) })
  assert.equal(signalOf(answered, 'mine', NOW, 90), null)
  assert.match(signalOf(conv({ status: 'closed', closed_at: iso(60_000) }), 'closed', NOW, 90)!.text, /^Closed /)
})

test('the lead level in words', () => {
  assert.equal(levelOf(lead({ tier: 'gold' })).name, 'Ready to quote')
  assert.equal(levelOf(lead({ tier: 'silver' })).name, 'Almost ready')
  assert.equal(levelOf(lead({ tier: 'bronze', departure_date: null })).name, 'Early interest')
  assert.equal(levelOf(lead({ origin_code: null, destination_code: null })).name, 'Contact only')
  assert.equal(levelOf(null).name, 'Contact only')
  assert.deepEqual(missingForReady(lead({ return_date: null, tier: 'silver' })), ['the return date', 'the client’s yes'])
  assert.deepEqual(missingForReady(lead({ trip_type: 'one_way', return_date: null, tier: 'gold' })), [])
})

test('the CRM card never claims what the server does not know', () => {
  const c = conv({ visitor_phone: '+1 555', visitor_email: 'm@x.com' })
  assert.equal(crmView(c, lead({ created_in_crm: true, crm_lead_id: '208131' }), { canAct: true, ownerName: null }).title, 'In the CRM · lead 208131')
  const blocked = crmView(conv({ tag: 'abandoned' }), lead(), { canAct: true, ownerName: null })
  assert.equal(blocked.state, 'blocked')
  const missing = crmView(conv(), lead({ visitor_phone: null, visitor_email: null }), { canAct: true, ownerName: null })
  assert.equal(missing.state, 'missing')
  assert.match(missing.text, /Still missing: a phone number and an e-mail\. Ask Michael in the chat\./)
  const ready = crmView(c, lead(), { canAct: true, ownerName: null })
  assert.equal(ready.state, 'ready')
  assert.match(ready.text, /Closing the chat doesn’t send it\. Send it now/)
  const theirs = crmView(c, lead(), { canAct: false, ownerName: 'Rohan Mehta' })
  assert.match(theirs.text, /Rohan can send it from the chat\./)
  const ai = crmView(conv({ mode: 'ai', visitor_phone: '+1', visitor_email: 'm@x.com' }), lead(), { canAct: true, ownerName: null })
  assert.match(ai.text, /^The assistant sends it when Michael confirms/)
  const refused = crmView(c, lead({ crm_push_gate_reason: 'email_typo_domain' }), { canAct: true, ownerName: null })
  assert.match(refused.text, /Last try was refused: the e-mail domain looks misspelled\./)
})

test('the summary is written once, at the 4th client message — the empty state says so', () => {
  assert.match(noSummaryText(conv(), 'mine', 2), /came to a person before that\. The trip below is what it collected\./)
  assert.equal(noSummaryText(conv(), 'waiting', 2), 'No summary yet. The assistant writes it once, at the client’s 4th message. Michael has sent 2.')
  assert.equal(noSummaryText(conv(), 'closed', 3), 'No summary. The chat ended before Michael’s 4th message.')
})

test('where they came from: site, ads, campaign words, page, device', () => {
  const rows = whereRows({
    site: 'bbc', gclid: 'x', utm_source: 'google', utm_medium: 'cpc', utm_term: 'business class london',
    page_url: 'https://buybusinessclass.com/business-class-flights/london', referrer: 'https://www.google.com/',
    user_agent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)',
  }, 'United States')
  assert.deepEqual(rows.map((r) => [r.label, r.value]), [
    ['Site', 'buybusinessclass.com'],
    ['Source', 'Google Ads · google · cpc'],
    ['Searched', '“business class london”'],
    ['Page', '/business-class-flights/london'],
    ['From', 'www.google.com'],
    ['Device', 'Phone'],
    ['Phone from', 'United States'],
  ])
  assert.deepEqual(whereRows(undefined), [])
})

test('list previews say who spoke', () => {
  assert.equal(previewText(conv({ last_message: { role: 'ai', content: 'Which dates?', created_at: iso(0) } })), 'AI: Which dates?')
  assert.equal(previewText(conv({ assigned_agent_name: 'Rohan Mehta', last_message: { role: 'agent', content: 'Hi', created_at: iso(0) } })), 'Rohan: Hi')
  assert.equal(previewText(conv({ last_message: { role: 'user', content: 'Thanks.', created_at: iso(0) } })), 'Thanks.')
  assert.equal(previewText(conv({ last_message: null })), '')
})

// ── a waiting chat is taken blind (owner, Nov 2026) ─────────────────

test('a waiting row says whether they asked for a person and how much they wrote — nothing else', () => {
  assert.equal(queueLine({ status: 'needs_agent', message_count: 6 }), 'Asked for a person')
  assert.equal(queueLine({ status: 'active', message_count: 1 }), '1 message')
  assert.equal(queueLine({ status: 'active', message_count: 4 }), '4 messages')
})

test('the waiting line never names the cities, shows no words, and opens nothing before Take', () => {
  const src = read('src/features/chats/components/queue-section.tsx')
  for (const leak of ['routeText', 'route', 'previewText', 'last_message', 'onOpen']) {
    assert.ok(!new RegExp(`\\b${leak}\\b`).test(src.replace(/\/\*[\s\S]*?\*\//g, '')), `queue-section must not use ${leak}`)
  }
  assert.match(src, /queueLine\(conv\)/)
  // Both layouts take from the line directly: the rail has its own Take.
  assert.equal(src.match(/onClick=\{\(\) => claim\(conv\)\}/g)?.length, 2)
  const page = read('src/features/chats/index.tsx')
  assert.ok(!/<QueueSection[^>]*onOpen/.test(page), 'the page must not wire a preview into the line')
})

test('blind take: the same rule as the server, for agents only', () => {
  const waiting = conv({ assigned_agent_id: null, status: 'needs_agent', mode: 'ai', queued_at: iso(60_000) })
  for (const role of ['sales', 'support']) assert.equal(takenBlind(waiting, ME, role), true)
  for (const role of ['owner', 'admin', 'dev', 'qa', 'supervisor', 'project_manager']) assert.equal(takenBlind(waiting, ME, role), false)
  // Released back into the line: mode "ai", but queued — still waiting for a person.
  assert.equal(takenBlind(conv({ assigned_agent_id: null, status: 'active', mode: 'ai', queued_at: iso(60_000) }), ME, 'sales'), true)
  assert.equal(takenBlind(conv({ assigned_agent_id: null, status: 'active', mode: 'human', queued_at: null }), ME, 'sales'), true)
  // Not blind: mine, someone else's, closed, the AI's with nobody waiting, or a chat I was talking in.
  assert.equal(takenBlind(conv({ assigned_agent_id: ME, status: 'needs_agent' }), ME, 'sales'), false)
  assert.equal(takenBlind(conv({ assigned_agent_id: 'other' }), ME, 'sales'), false)
  assert.equal(takenBlind(conv({ assigned_agent_id: null, status: 'closed', queued_at: iso(60_000) }), ME, 'sales'), false)
  assert.equal(takenBlind(conv({ assigned_agent_id: null, status: 'active', mode: 'ai', queued_at: null }), ME, 'sales'), false)
  assert.equal(takenBlind(conv({ assigned_agent_id: null, status: 'needs_agent', metadata: { engaged_agent_id: ME } }), ME, 'sales'), false)
})

test('an agent who lands on a waiting chat sees who and how long, not the chat', () => {
  const src = read('src/features/chats/detail.tsx')
  assert.match(src, /const blindView = !!conv && takenBlind\(conv, me\?\.accountNo, role\)/)
  // The gate returns before the thread and the details are built.
  const gate = src.indexOf('if (blindView) {')
  assert.ok(gate > 0 && gate < src.indexOf('const details = ('), 'the gate comes before the details')
  assert.ok(gate < src.indexOf("aria-label='Messages'"), 'the gate comes before the thread')
  // Nothing about it is polled or streamed for them: messages, presence,
  // typing, and v2's trip card (/details — route, dates, travelers).
  assert.equal(src.match(/enabled: !!conv && !blindView && activeTab === 'my_active'/g)?.length, 4)
  assert.match(src, /queryFn: \(\) => getConversationDetails\(conversationId\),\n(\s*\/\/.*\n)*\s*enabled: !!conv && !blindView/)
  assert.match(src, /usingMock \|\| blindView\) return/)
})

// ── the Trip card: what the old panel showed, plus the answers it kept ──

test('dates carry the year; the route is the codes', () => {
  assert.equal(fullDate('2026-11-25'), '25 Nov 2026')
  assert.equal(fullDate('2027-01-04T00:00:00Z'), '4 Jan 2027')
  assert.equal(fullDate(null), '')
  assert.equal(fullDate('not a date'), '')
  assert.equal(routeLine(lead()), 'JFK → LHR')
  assert.equal(routeLine(lead({ destination_code: null })), 'JFK')
  assert.equal(routeLine(null), '')
})

test('travelers: the total, and the split the CRM gets when children fly', () => {
  assert.equal(travelersText({ passengers: 1 }), '1 traveler')
  assert.equal(travelersText({ passengers: 2, children_count: 0, infant_count: 0 }), '2 travelers')
  assert.equal(travelersText({ passengers: 3, children_count: 1 }), '3 travelers · 2 adults, 1 child')
  assert.equal(travelersText({ passengers: 4, children_count: 2, infant_count: 1 }), '4 travelers · 1 adult, 2 children, 1 infant')
  assert.equal(travelersText({ passengers: null }), '')
})

test('the notes split the way the pipeline writes them', () => {
  const n = notesOf(lead({
    notes: 'CLIENT ASKED TO BE CALLED — +1 555 0142\nItinerary: JFK → LHR → CDG → JFK\nChat: Type=value_driven | Budget~$6000\nPrefers an aisle seat',
  }))
  assert.deepEqual(n, { itinerary: 'JFK → LHR → CDG → JFK', callback: '+1 555 0142', other: ['Prefers an aisle seat'] })
  assert.equal(notesOf(lead({ notes: 'CLIENT ASKED TO BE CALLED — number not captured' })).callback, '')
  assert.deepEqual(notesOf(lead({ notes: 'Chat: Dates=flexible' })), { itinerary: '', callback: null, other: [] })
  assert.deepEqual(notesOf(null), { itinerary: '', callback: null, other: [] })
})

test('trip rows: the itinerary first, dates to copy, flexible only when the client said so', () => {
  const rows = tripRows(
    lead({
      trip_type: 'multi_city', passengers: 3, children_count: 1, flexible_dates: true,
      notes: 'Itinerary: JFK → LHR → CDG → JFK\nChat: Budget~$6000',
    }),
    { nonstop_only: true, airline_preference: 'Qatar', airline_avoid: 'Spirit', budget_hint: '$6000', price_seen: '$4800', must_haves: 'lie-flat seat' }
  )
  assert.deepEqual(rows.map((r) => [r.label, r.value]), [
    ['Itinerary', 'JFK → LHR → CDG → JFK'],
    ['Out', '16 Nov 2026'],
    ['Back', '23 Nov 2026'],
    ['Travelers', '3 travelers · 2 adults, 1 child'],
    ['Cabin', 'Business'],
    ['Trip', 'Multi-city · direct only'],
    ['Airlines', 'Qatar'],
    ['Not with', 'Spirit'],
    ['Budget', '$6000'],
    ['Price seen', '$4800'],
    ['Must have', 'lie-flat seat'],
  ])
  assert.deepEqual(rows.filter((r) => r.copy).map((r) => r.copy), [
    { text: 'JFK → LHR → CDG → JFK', what: 'Itinerary' },
    { text: '16 Nov 2026', what: 'Departure date' },
    { text: '23 Nov 2026', what: 'Return date' },
  ])
  // flexible_dates is true on every lead: alone it says nothing.
  const label = (sig: Record<string, unknown>, over: Partial<Lead> = {}) =>
    tripRows(lead({ flexible_dates: true, ...over }), sig).find((r) => r.label === 'Dates')?.value
  assert.equal(label({}), undefined)
  assert.equal(label({ date_flexible: true }), 'Flexible')
  assert.equal(label({ date_flexible: false }), 'Fixed')
  assert.equal(label({}, { flexible_dates: false }), 'Fixed')
  assert.equal(tripRows(lead({ trip_type: 'one_way', return_date: null }), {})[0].label, 'When')
  assert.deepEqual(tripRows(null, {}), [])
})

test('copy the trip: the route, then every row as label and value', () => {
  const rows = tripRows(lead({ notes: '', flexible_dates: true }), {})
  assert.equal(
    tripText(routeLine(lead()), rows),
    'JFK → LHR\nOut: 16 Nov 2026\nBack: 23 Nov 2026\nTravelers: 2 travelers\nCabin: Business\nTrip: Round trip'
  )
  assert.equal(tripText('', []), '')
})

test('about the client: the call back carries its number; airlines live on the trip now', () => {
  const withNumber = aboutOf({ callback_requested: true, airline_preference: 'Qatar', booking_for: 'parents', best_call_time: 'after 6 pm' }, lead({ notes: 'CLIENT ASKED TO BE CALLED — +1 555 0142' }))
  assert.deepEqual(withNumber.rows.map((r) => [r.label, r.value]), [
    ['Booking for', 'parents'],
    ['Asks', 'A call back on +1 555 0142'],
    ['Best time', 'after 6 pm'],
  ])
  assert.deepEqual(withNumber.rows[1].copy, { text: '+1 555 0142', what: 'Number' })
  assert.deepEqual(aboutOf({ callback_requested: true }).rows, [{ label: 'Asks', value: 'A call back' }])
  assert.deepEqual(aboutOf({}, lead({ notes: 'CLIENT ASKED TO BE CALLED — number not captured' })).rows, [{ label: 'Asks', value: 'A call back' }])
  assert.deepEqual(aboutOf({}).rows, [])
})
