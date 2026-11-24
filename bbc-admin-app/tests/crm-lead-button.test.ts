// The agent's "Create lead in CRM" button — pinned on the pure helpers in features/chats/view.ts.
// Runs with: node --experimental-strip-types --test tests/crm-lead-button.test.ts
// Every name, number and address here is invented.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import type { Conversation, CrmLeadStatus, Lead } from '../src/lib/types.ts'
import { crmButtonView, crmView, safeCrmUrl } from '../src/features/chats/view.ts'

const off: CrmLeadStatus = { enabled: false, lead: null, can_create: false, reason: null }

test('off for this user: nothing under the chat', () => {
  assert.equal(crmButtonView(off).show, false)
  assert.equal(crmButtonView(undefined).show, false)
  assert.equal(crmButtonView({ ...off, can_create: true }).show, false, 'a stray can_create never shows a switched-off button')
})

test('the button when the chat can make its lead', () => {
  const v = crmButtonView({ enabled: true, lead: null, can_create: true, reason: null })
  assert.equal(v.show, true)
  assert.equal(v.canCreate, true)
  assert.equal(v.linkText, null)
})

test('why not yet, in the server’s words', () => {
  const v = crmButtonView({ enabled: true, lead: null, can_create: false, reason: 'No phone or e-mail yet: ask Mara in the chat.' })
  assert.equal(v.canCreate, false)
  assert.equal(v.reason, 'No phone or e-mail yet: ask Mara in the chat.')
})

test('another agent’s chat: nothing to say', () => {
  assert.equal(crmButtonView({ enabled: true, lead: null, can_create: false, reason: null }).show, false)
})

test('the lead made: its link and the CRM’s warnings', () => {
  const v = crmButtonView({
    enabled: true,
    can_create: false,
    reason: null,
    lead: {
      flight_request_id: 98765,
      url: 'https://crm.example.test/requests/98765',
      created: true,
      warnings: [
        { code: 'ASSIGNED_TO_CLIENT_OWNER', message: 'This client belongs to another agent.' },
        { code: 'TRIP_IGNORED', message: '' },
      ],
    },
  })
  assert.equal(v.linkText, 'Open lead in CRM · #98765')
  assert.equal(v.url, 'https://crm.example.test/requests/98765')
  assert.deepEqual(v.notes, ['This client belongs to another agent.', 'TRIP_IGNORED'])
  assert.equal(v.canCreate, false)
})

test('only an https address becomes a link', () => {
  assert.equal(safeCrmUrl('javascript:alert(1)'), null)
  assert.equal(safeCrmUrl('http://crm.example.test/requests/1'), null)
  assert.equal(safeCrmUrl('not a url'), null)
  assert.equal(safeCrmUrl(null), null)
  assert.equal(safeCrmUrl('https://crm.example.test/requests/1'), 'https://crm.example.test/requests/1')
})

const conv = (patch: Partial<Conversation> = {}) =>
  ({ id: 'c-1', status: 'active', mode: 'human', visitor_name: 'Mara Test', visitor_email: null, visitor_phone: '+14155550123', metadata: {}, ...patch }) as unknown as Conversation
const lead = (patch: Partial<Lead> = {}) =>
  ({ id: 'L1', conversation_id: 'c-1', created_in_crm: false, visitor_email: null, visitor_phone: '+14155550123', ...patch }) as unknown as Lead

test('with the button on, one contact is enough and the details point at the button', () => {
  const on = crmView(conv(), lead(), { canAct: true, ownerName: null, button: true })
  assert.equal(on.state, 'ready')
  assert.match(on.text, /Create lead in CRM/)
  const offView = crmView(conv(), lead(), { canAct: true, ownerName: null })
  assert.equal(offView.state, 'missing', 'the old Send to CRM still needs both')
})

test('with the button on and no contact at all, ask for one', () => {
  const v = crmView(conv({ visitor_phone: null }), lead({ visitor_phone: null }), { canAct: true, ownerName: null, button: true })
  assert.equal(v.state, 'missing')
  assert.match(v.text, /phone or an e-mail\. Ask Mara in the chat\./)
})

test('a lead already in the CRM reads the same with the button on', () => {
  const v = crmView(conv(), lead({ created_in_crm: true, crm_lead_id: '98765' }), { canAct: true, ownerName: null, button: true })
  assert.equal(v.state, 'in')
  assert.equal(v.title, 'In the CRM · lead 98765')
})
