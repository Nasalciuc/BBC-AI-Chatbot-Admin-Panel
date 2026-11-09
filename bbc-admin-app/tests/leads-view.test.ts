// What the Leads page says — pinned on features/leads/view.ts, plus the
// chat panel's level now following the client's YES.
// Runs with: node --experimental-strip-types --test tests/leads-view.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import type { Lead, LeadRow } from '../src/lib/types.ts'
import { ageText, agoText, checklist, crmLine, historyOf, leadsCsv, missingText, summaryCards, tripLine } from '../src/features/leads/view.ts'
import { confirmedOf, levelOf, missingForReady } from '../src/features/chats/view.ts'

const NOW = Date.parse('2026-11-06T12:00:00Z')

function row(over: Partial<LeadRow> = {}): LeadRow {
  return {
    id: 'L1', conversation_id: 'c1', chat_number: 4810, created_at: '2026-11-06T11:54:00Z',
    origin_code: 'JFK', destination_code: 'LHR', route_display: 'New York → London',
    departure_date: '2026-11-16', return_date: '2026-11-23', passengers: 2, children_count: 0, infant_count: 0,
    trip_type: 'round_trip', cabin_class: 'business', flexible_dates: false, score: 60, tier: 'silver', status: 'new',
    created_in_crm: false, created_in_crm_at: null, crm_lead_id: null, crm_push_gate_reason: null,
    reviewed_by_qa: false, reviewed_at: null, reviewed_by: null,
    visitor_name: 'Michael Turner', visitor_email: 'michael.turner@example.com', visitor_phone: '+1 555 0110',
    conversation_status: 'active', conversation_mode: 'human', tunnel: 'sales', assigned_agent_id: 'v',
    last_agent_message_at: null, conversation_closed_at: null, confirmed_at: null, closed_by_agent_id: null,
    level: 'almost', chat_with: 'person', crm_state: 'open', agent_name: 'Victor Rusu', closed_by_name: null,
    ...over,
  }
}

test('the CRM column says why a lead is not there', () => {
  assert.deepEqual(crmLine(row()), { text: 'Ready to send · chat open', tone: 'ink' })
  assert.deepEqual(crmLine(row({ crm_state: 'in_crm', crm_lead_id: '208131' })), { text: 'In the CRM · lead 208131', tone: 'ok' })
  assert.equal(crmLine(row({ crm_state: 'refused', crm_push_gate_reason: 'email_typo_domain' })).text, 'Refused: the e-mail domain looks misspelled')
  assert.equal(crmLine(row({ crm_state: 'refused', crm_push_gate_reason: 'duplicate_contact' })).text, 'Refused: another chat with the same person was sent instead')
  assert.equal(crmLine(row({ crm_state: 'closed_by_agent', closed_by_name: 'Ana Ceban' })).text, 'Not sent: Ana Ceban closed the chat')
  assert.equal(crmLine(row({ crm_state: 'ready_never_sent' })).text, 'Ready, never reached the CRM')
  assert.equal(crmLine(row({ crm_state: 'no_phone' })).tone, 'muted')
})

test('age, trip and the summary cards', () => {
  assert.equal(ageText('2026-11-06T11:54:00Z', NOW), '6 min')
  assert.equal(ageText('2026-11-06T10:50:00Z', NOW), '1 h 10 m')
  assert.equal(ageText('2026-11-03T12:00:00Z', NOW), '3 d')
  assert.equal(ageText('2026-10-12T12:00:00Z', NOW), '12 Oct')
  // In a sentence, "ago" goes only after a duration.
  assert.equal(agoText('2026-11-06T11:54:00Z', NOW), '6 min ago')
  assert.equal(agoText('2026-11-03T12:00:00Z', NOW), '3 d ago')
  assert.equal(agoText(new Date(NOW - 20_000).toISOString(), NOW), 'just now')
  assert.equal(agoText('2026-10-12T12:00:00Z', NOW), '12 Oct')
  assert.equal(agoText(null, NOW), '')
  assert.deepEqual(tripLine(row()), { route: 'JFK → LHR', detail: '16 Nov – 23 Nov · 2 adults · Business' })
  assert.equal(tripLine(row({ passengers: 3, children_count: 1 })).detail, '16 Nov – 23 Nov · 2 adults, 1 child · Business')
  assert.equal(tripLine(row({ origin_code: null, destination_code: null })).route, 'No route')
  assert.deepEqual(
    summaryCards({ open: 3, closed_by_agent: 1, ready_never_sent: 1, never_sent: 0, refused: { same_route: 1 } }).map((c) => `${c.n} ${c.text}`),
    ['3 still in an open chat', '1 closed without sending', '1 ready, never sent', '1 refused: the departure and the destination are the same']
  )
})

test('the level checklist and what is missing for Ready', () => {
  const c = checklist(row({ return_date: null }))
  assert.deepEqual(c.map((x) => x.ok), [true, true, true, false, true, false])
  assert.equal(missingText(row({ return_date: null })), 'Missing for Ready: return date (or one way), confirmed by the client.')
  assert.equal(missingText(row({ level: 'ready', confirmed_at: '2026-11-06T11:58:00Z' })), '')
  assert.equal(checklist(row({ crm_state: 'refused', crm_push_gate_reason: 'phone_format' }))[4].note, 'the phone number doesn’t look valid')
})

test('history is newest first and names who did it', () => {
  const h = historyOf(
    row({
      confirmed_at: '2026-11-06T11:58:00Z', created_in_crm: true, created_in_crm_at: '2026-11-06T11:58:30Z', crm_lead_id: '9',
      reviewed_by_qa: true, reviewed_at: '2026-11-07T09:00:00Z',
    }),
    'Elena Vasile'
  )
  assert.deepEqual(h.map((x) => x.text), [
    'Reviewed by Elena Vasile',
    'Sent to the CRM · lead 9',
    'Michael confirmed the trip to the assistant',
    'Lead created from chat #4810',
  ])
})

test('CSV quotes everything and defuses formulas', () => {
  const csv = leadsCsv([row({ visitor_name: '=HYPERLINK("x")' })])
  const [head, line] = csv.split('\n')
  assert.match(head, /^"Name","Phone","E-mail","Route"/)
  assert.ok(line.startsWith(`"'=HYPERLINK(""x"")"`))
})

test('the chat panel’s level follows the client’s YES, not the score', () => {
  const lead = { origin_code: 'JFK', destination_code: 'LHR', departure_date: '2026-11-16', return_date: null, passengers: 2, trip_type: 'round_trip', tier: 'gold' } as Lead
  assert.equal(levelOf(lead, false).name, 'Almost ready')
  assert.equal(levelOf(lead, true).name, 'Ready to quote')
  // Older payloads without metadata: the gold tier stands in.
  assert.equal(levelOf(lead).name, 'Ready to quote')
  assert.deepEqual(missingForReady(lead, false), ['the return date', 'the client’s yes'])
  assert.equal(confirmedOf({ metadata: { confirmed_at: '2026-11-06T10:00:00Z' } }), true)
  assert.equal(confirmedOf({ metadata: {} }), false)
  assert.equal(confirmedOf(null), undefined)
})
