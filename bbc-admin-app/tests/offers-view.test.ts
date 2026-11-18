// Offers to send (ADR-6b, ADR-8b) and "the client left, without a contact" (ADR-6c): what the panel says.
// Server halves: bbc-chatbot-api app/services/offers_view.py and app/api/offers.py.
// Runs with: node --experimental-strip-types --test tests/offers-view.test.ts
// Every name, contact and route here is invented.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import type { OfferRow } from '../src/lib/types.ts'
import {
  channelLine,
  chatsNote,
  clientName,
  crmNote,
  doneLine,
  emptyText,
  markedText,
  otherChats,
  refusedLine,
  summaryText,
  tripLine,
  waitingText,
  whoLine,
  windowOf,
} from '../src/features/tasks/offers.ts'
import { gateReasonText, leftNote, phoneToCheck, presenceLine, presenceRank, presenceView } from '../src/features/chats/view.ts'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p: string) => readFileSync(join(root, p), 'utf-8')

const row = (over: Partial<OfferRow> = {}): OfferRow => ({
  conversation_id: 'c-1',
  lead_id: 'l-1',
  chat_number: 104,
  client: 'Dana Example',
  email: 'dana@example.com',
  phone: null,
  phone_to_check: null,
  route: 'NYC → LON',
  departure_date: '2026-12-10',
  return_date: '2026-12-17',
  trip_type: 'round_trip',
  passengers: 2,
  children_count: 0,
  infant_count: 0,
  cabin_class: 'business',
  chats: [{ conversation_id: 'c-1', chat_number: 104 }],
  blind: false,
  channels: [],
  refused: [],
  price_seen: null,
  crm_state: 'no_phone',
  crm_gate: null,
  crm_lead_id: null,
  agent_name: null,
  chat_status: 'closed',
  waiting_since: '2026-11-18T10:00:00+00:00',
  ...over,
})

test('how to send it: the channel the client asked for, else what the contact allows', () => {
  assert.deepEqual(channelLine(row({ channels: ['whatsapp'] })), { text: 'Asked for WhatsApp', kind: 'whatsapp' })
  assert.deepEqual(channelLine(row({ channels: ['whatsapp', 'email'] })), { text: 'Asked for WhatsApp or e-mail', kind: 'whatsapp' })
  assert.equal(channelLine(row({ channels: ['text', 'call', 'email'] })).text, 'Asked for a text, a call back or e-mail')
  assert.deepEqual(channelLine(row()), { text: 'Only an e-mail: send it there', kind: 'email' })
  assert.equal(channelLine(row({ email: null, phone: '+12105550100' })).text, 'Only a phone: call or text')
  assert.equal(channelLine(row({ phone: '+12105550100' })).text, 'No channel asked: phone or e-mail')
  assert.equal(channelLine(row({ email: null, phone_to_check: '0412 345 678' })).text, 'Only a phone: call or text')
  assert.equal(channelLine(row({ blind: true, email: null })).text, 'No channel asked yet')
})

test('the channels turned down', () => {
  assert.equal(refusedLine(['no calls', 'no texts']), 'No calls · No texts')
  assert.equal(refusedLine([]), '')
})

test('how long the client has waited', () => {
  const at = Date.parse('2026-11-18T10:00:00Z')
  assert.equal(waitingText('2026-11-18T10:00:00+00:00', at + 30_000), 'Just now')
  assert.equal(waitingText('2026-11-18T10:00:00+00:00', at + 5 * 60_000), 'Waiting 5 min')
  assert.equal(waitingText('2026-11-18T10:00:00+00:00', at + 200 * 60_000), 'Waiting 3 h 20 min')
  assert.equal(waitingText('2026-11-18T10:00:00+00:00', at + 3 * 86_400_000), 'Waiting 3 d')
  assert.equal(waitingText('not a time', at), 'Just now')
})

test('who, and the trip — never on a blind row', () => {
  assert.equal(clientName(row()), 'Dana Example')
  assert.equal(clientName(row({ client: null })), 'Chat #104')
  assert.equal(clientName(row({ client: null, chat_number: null })), 'A client')
  assert.equal(tripLine(row()), 'NYC → LON · 10 Dec – 17 Dec · 2 travelers · Business')
  assert.equal(tripLine(row({ return_date: null, passengers: 1, cabin_class: 'premium_economy' })), 'NYC → LON · 10 Dec · 1 traveler · Premium economy')
  assert.equal(
    tripLine(row({ blind: true, route: null, departure_date: null })),
    'Waiting for a person in the chats: take it there to see the trip and the contact.',
  )
})

test('who talked to the client, and the CRM in the Leads page’s words', () => {
  assert.equal(whoLine(row()), 'Only with the assistant')
  assert.equal(whoLine(row({ agent_name: 'Agent One', chat_status: 'active' })), 'With Agent · chat still open')
  assert.deepEqual(crmNote(row()), { text: 'No phone: can’t be sent', tone: 'muted' })
  assert.deepEqual(crmNote(row({ crm_state: 'refused', crm_gate: 'crm_422:client.email' })), { text: 'Refused: the CRM rejected the e-mail', tone: 'bad' })
  assert.deepEqual(crmNote(row({ crm_state: 'in_crm', crm_lead_id: '5001' })), { text: 'In the CRM · lead 5001', tone: 'ok' })
  assert.equal(crmNote(row({ blind: true, crm_state: null })), null, 'a blind row keeps the CRM’s verdict back')
})

test('one client, several chats: one mark for all of them', () => {
  const two = row({ chats: [{ conversation_id: 'c-1', chat_number: 104 }, { conversation_id: 'c-2', chat_number: 99 }] })
  assert.equal(chatsNote(two), '2 chats')
  assert.equal(chatsNote(row()), '')
  assert.deepEqual(otherChats(two), ['c-2'])
  assert.deepEqual(otherChats(row()), [])
})

test('the marked list, the counts, the empty queue and the toast', () => {
  const now = Date.parse('2026-11-18T15:00:00Z')
  assert.equal(doneLine({ state: 'sent', by_name: 'Agent One', at: '2026-11-18T14:42:00+00:00' }, now, 'UTC'), 'Sent · Agent One · 14:42')
  assert.equal(doneLine({ state: 'not_needed', by_name: null, at: '2026-11-17T09:05:00+00:00' }, now, 'UTC'), 'Not needed · Tue 17 Nov, 09:05')
  assert.equal(summaryText({ waiting: 4, sent: 2, not_needed: 0 }), '4 waiting · 2 sent')
  assert.equal(summaryText({ waiting: 4, sent: 2, not_needed: 1 }), '4 waiting · 2 sent · 1 not needed')
  assert.match(emptyText(3), /from the last 3 days/)
  assert.match(emptyText(1), /from the last 24 hours/)
  assert.equal(markedText('sent', 'Dana Example'), 'Offer marked sent: Dana Example.')
  assert.equal(markedText('open', 'Dana Example'), 'Back in the queue: Dana Example.')
  assert.equal(windowOf('7'), 7)
  assert.equal(windowOf('5'), 3)
  assert.equal(windowOf(null), 3)
})

test('the page opens on the queue for exactly the roles the server lets read it', () => {
  const page = read('src/features/tasks/index.tsx')
  const panelRoles = /const OFFER_READERS = \[([^\]]+)\]/.exec(page)?.[1].match(/'([a-z_]+)'/g)?.map((x) => x.slice(1, -1)).sort()
  const server = read('../bbc-chatbot-api/app/api/offers.py')
  const serverRoles = /READERS = \{([^}]+)\}/.exec(server)?.[1].match(/"([a-z_]+)"/g)?.map((x) => x.slice(1, -1)).sort()
  assert.ok(panelRoles?.length)
  assert.deepEqual(panelRoles, serverRoles)
  // A link to a task still opens the task list.
  assert.match(page, /search\.tab === 'tasks' \|\| search\.task \|\| !canOffers/)
  assert.match(read('src/routes/_authenticated/tasks/index.tsx'), /tab: z\.enum\(\['offers', 'tasks', 'rules'\]\)/)
})

test('B3: a client who left says whether a message can still reach them', () => {
  const left = presenceView('left', 600)
  assert.equal(presenceLine(left, false), 'Left 10 min ago · no contact')
  assert.equal(presenceLine(left, true), 'Left 10 min ago · contact saved')
  assert.equal(presenceLine(left, undefined), 'Left 10 min ago', 'an older server: as before')
  assert.equal(presenceLine(presenceView('online', 5), false), 'On the site · chat open', 'still here: no contact note')
  assert.equal(presenceLine(presenceView('left', 30), false), 'Left just now', 'a page change looks like that at first')
  assert.match(leftNote('Dana', 3, false), /^Dana left the site 3 min ago, without a contact: a message here won’t reach them\. If they don’t come back, the chat closes by itself in about 2 min\.$/)
  assert.match(leftNote('Dana', 7, true), /Their contact is saved: the written offer goes out from Tasks → Offers to send\. .*any moment\.$/)
  assert.equal(leftNote('Dana', 3, undefined), 'Dana left the site 3 min ago. If they don’t come back, the chat closes by itself in about 2 min.')
})

test('B3: clients still here come first, the ones who left last', () => {
  assert.equal(presenceRank(presenceView('online', 5)), 0)
  assert.equal(presenceRank(presenceView('away', 120)), 0)
  assert.equal(presenceRank(presenceView('left', 30)), 0, 'left just now is still here')
  assert.equal(presenceRank(presenceView('stale', 300)), 1)
  assert.equal(presenceRank(presenceView(null, null)), 1)
  assert.equal(presenceRank(presenceView('left', 600)), 2)
  const list = read('src/features/chats/index.tsx')
  assert.match(list, /here\(a\) - here\(b\) \|\| urgency\(a\) - urgency\(b\) \|\| idle\(a\) - idle\(b\)/)
  // The two groups of All active are filtered, not sliced: each keeps the clients still here first.
  assert.doesNotMatch(list, /rows\.slice\(0, nWait\)/)
})

test('ADR-6f: a number to check, only while there is no phone', () => {
  assert.equal(phoneToCheck({ visitor_phone: null, metadata: { phone_to_check: '0412 345 678' } }), '0412 345 678')
  assert.equal(phoneToCheck({ visitor_phone: '+61412345678', metadata: { phone_to_check: '0412 345 678' } }), null)
  assert.equal(phoneToCheck({ visitor_phone: null, metadata: {} }), null)
  assert.equal(phoneToCheck({ visitor_phone: null }), null)
})

test('ADR-8d: why the CRM refused, in words', () => {
  assert.equal(gateReasonText('crm_422:client.email'), 'the CRM rejected the e-mail')
  assert.equal(gateReasonText('crm_422:client.email,client.phone'), 'the CRM rejected the e-mail and the phone')
  assert.equal(gateReasonText('crm_422:client.first_name,client.last_name'), 'the CRM rejected the name')
  assert.equal(gateReasonText('crm_422:flight.departure_date'), 'the CRM rejected flight departure date')
  assert.equal(gateReasonText('crm_422:unknown'), 'the CRM rejected it without saying why')
  assert.equal(gateReasonText('email_missing'), 'the CRM needs an e-mail too')
  assert.equal(gateReasonText('no_contact'), 'there’s no e-mail or phone')
  assert.equal(gateReasonText('phone_format'), 'the phone number doesn’t look valid')
})
