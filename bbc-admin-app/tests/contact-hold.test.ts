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
  takeClass,
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

test('during the hold Take stays pressable and the server decides; a refusal reads the chat again', () => {
  // CodeRabbit #282: the hold ends early on the server (a contact, an ask for a person) and nothing tells a waiting
  // chat's screen (no stream, no timer: PR-10). Shut until the cached deadline, Take kept the agent out of a chat the
  // server would give. Now it is light while held, says when it opens, and a press the server refuses says why.
  assert.equal(takeClass(true), 'bg-k-ffffff text-k-202020 shadow-[0_0_0_1px_var(--k-ececec)]')
  assert.equal(takeClass(false), 'bg-kb-202020 text-on-btn')
  const detail = src('src/features/chats/detail.tsx')
  assert.equal((detail.match(/onClick=\{handleClaim\} disabled=\{taking\} className=\{cn\('h-11 rounded-full px-6 text-\[15px\] font-bold disabled:opacity-50', takeClass\(heldS > 0\)\)\}/g) ?? []).length, 2, 'the blind view and the waiting block')
  assert.doesNotMatch(detail, /heldS > 0\}/)
  assert.match(detail, /takeRefusal\(/)
  // A refused press reads the chat again: the toast says the server's time, the button follows it.
  assert.match(detail, /toast\.info\(holdLine\(firstName\(conv\?\.visitor_name\), refusal\.holdSeconds\)\)[\s\S]{0,300}queryClient\.invalidateQueries\(\{ queryKey: \['conversation', conversationId\] \}\)/)
  const queue = src('src/features/chats/components/queue-section.tsx')
  assert.equal((queue.match(/disabled=\{busyId === conv\.id\}/g) ?? []).length, 2, 'the rail and the list')
  assert.equal((queue.match(/takeClass\(held > 0\)/g) ?? []).length, 2)
  assert.match(queue, /takeRefusal\(err\.message\)/)
  assert.match(queue, /queryClient\.invalidateQueries\(\{ queryKey: \['queue-rows'\] \}\)/)
  assert.match(queue, /ticking \? 1_000 : 15_000/)
})
