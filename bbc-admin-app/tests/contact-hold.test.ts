// Take waits while the AI gets the client's contact (the server's contact hold, 25 Nov 2026), and the agent's
// first message asks for a channel. Runs with: node --experimental-strip-types --test tests/contact-hold.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  FIRST_MESSAGE,
  firstMessageHint,
  holdButton,
  holdLeft,
  holdLine,
  takeRefusal,
} from '../src/features/chats/view.ts'

const here = dirname(fileURLToPath(import.meta.url))
const src = (p: string) => readFileSync(join(here, '..', p), 'utf8')
const NOW = Date.parse('2026-11-25T12:00:00Z')

test('a held chat counts down in whole seconds to the time the server gave', () => {
  assert.equal(holdLeft({ contact_hold_until: '2026-11-25T12:01:30Z' }, NOW), 90)
  assert.equal(holdLeft({ contact_hold_until: '2026-11-25T12:00:00.400Z' }, NOW), 1)
})

test('a chat with no hold, a hold already over, or an unreadable time is open to Take', () => {
  assert.equal(holdLeft({ contact_hold_until: null }, NOW), 0)
  assert.equal(holdLeft({}, NOW), 0)
  assert.equal(holdLeft({ contact_hold_until: '2026-11-25T11:59:00Z' }, NOW), 0)
  assert.equal(holdLeft({ contact_hold_until: 'soon' }, NOW), 0)
})

test('the held chat says what the AI is doing and when Take opens', () => {
  assert.equal(holdLine('Maria', 72), 'The AI is asking Maria for a phone or email. You can take the chat in 1:12.')
  assert.equal(holdButton(72), 'Take in 1:12')
})

test('a 409 from Take reads as the hold, as a colleague who won, or as neither', () => {
  const held = JSON.stringify({ detail: { detail: 'contact_hold', retry_after: 41, hold_until: '2026-11-25T12:00:41Z' } })
  assert.deepEqual(takeRefusal(held), { holdSeconds: 41 })
  const lost = JSON.stringify({ detail: { detail: 'already_claimed', winner: 'Bogdan' } })
  assert.deepEqual(takeRefusal(lost), { winner: 'Bogdan' })
  assert.deepEqual(takeRefusal('This one is already taken'), {})
  assert.deepEqual(takeRefusal(JSON.stringify({ detail: { detail: 'contact_hold', retry_after: 0 } })), {})
})

test('the first-message hint shows only before any agent word, in a chat with no contact', () => {
  assert.equal(firstMessageHint([{ role: 'user' }, { role: 'ai' }], false), true)
  assert.equal(firstMessageHint([{ role: 'user' }, { role: 'ai' }], undefined), true)
  assert.equal(firstMessageHint([{ role: 'user' }, { role: 'ai' }], true), false)
  assert.equal(firstMessageHint([{ role: 'user' }, { role: 'agent' }], false), false)
})

test('the first message names one channel and a reason, with no long dash', () => {
  assert.match(FIRST_MESSAGE, /mobile number\?/)
  assert.match(FIRST_MESSAGE, /fares/)
  assert.ok(!FIRST_MESSAGE.includes('—'))
})

test('both Take buttons wait for the hold, and both read the 409 the same way', () => {
  const detail = src('src/features/chats/detail.tsx')
  assert.equal((detail.match(/disabled=\{taking \|\| heldS > 0\}/g) ?? []).length, 2, 'the blind view and the waiting block')
  assert.match(detail, /takeRefusal\(/)
  const queue = src('src/features/chats/components/queue-section.tsx')
  assert.equal((queue.match(/disabled=\{busyId === conv\.id \|\| held > 0\}/g) ?? []).length, 2, 'the rail and the list')
  assert.match(queue, /takeRefusal\(err\.message\)/)
  assert.match(queue, /ticking \? 1_000 : 15_000/)
})
