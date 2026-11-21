// The follow-up lottery in the offers queue (src/features/tasks/offers.ts): how a lead reached its agent, in one
// line, and the "Mine" view. Server half: bbc-chatbot-api app/services/offers_view.py (followup_line).

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { followupText, mineOnly } from '../src/features/tasks/offers.ts'

const at = '2026-11-21T09:12:00Z'
const hhmm = () => '09:12'

test('each state of the lottery says who and how', () => {
  assert.equal(followupText({ by: 'lottery', state: 'pending', at, agent_name: 'Ana Agent' }, hhmm),
    'Given by the lottery to Ana at 09:12: waiting for Ana to open it')
  assert.equal(followupText({ by: 'lottery', state: 'acked', at, agent_name: 'Ana Agent' }, hhmm), 'Ana took it from the lottery at 09:12')
  assert.equal(followupText({ by: 'manager', state: 'manual', at, agent_name: 'Bob Builder' }, hhmm), 'Given to Bob by a manager at 09:12')
  assert.match(followupText({ by: 'lottery', state: 'missed', at, agent_name: 'Ana Agent' }, hhmm), /didn’t open it in time/)
})

test('no line for a lead that did not come through the lottery', () => {
  assert.equal(followupText(null), null)
  assert.equal(followupText(undefined), null)
  assert.equal(followupText({ by: 'lottery', state: 'pending', at, agent_name: null }), null)
  assert.equal(followupText({ by: 'lottery', state: 'weird', at, agent_name: 'Ana' }), null)
})

test('mine is mine, and nobody is nothing', () => {
  const rows = [{ agent_id: 'me' }, { agent_id: 'other' }, { agent_id: null }, {}]
  assert.deepEqual(mineOnly(rows, 'me'), [{ agent_id: 'me' }])
  assert.deepEqual(mineOnly(rows, undefined), [])
})
