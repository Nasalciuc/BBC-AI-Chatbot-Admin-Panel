// The before-you-go after an agent (lever 6 of "Mai multe chaturi și leaduri", owner, 25 Nov 2026) and with the
// channel field left empty (lever 5's gap). Runs with: node --experimental-strip-types --test tests/before-you-go.test.mjs

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { NOT_YET, NOT_YET_WAIT_MS, answeredAgent, mayAsk } from '../src/beforeYouGo.ts'
import { askBeforeYouGo } from '../src/api.ts'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const chat = readFileSync(join(SRC, 'ChatWindow.tsx'), 'utf8')

const free = { busy: false, field: 'none' }
const alex = [{ role: 'ai' }, { role: 'user' }, { role: 'ai' }]
const unanswered = [{ role: 'ai' }, { role: 'user' }, { role: 'agent' }]
const answered = [{ role: 'ai' }, { role: 'user' }, { role: 'agent' }, { role: 'user' }]

test('the client answered the agent when they wrote after the agent\'s newest message', () => {
  assert.equal(answeredAgent(unanswered), false)
  assert.equal(answeredAgent(answered), true)
  assert.equal(answeredAgent([...answered, { role: 'agent' }]), false, 'the agent wrote again, unanswered')
  assert.equal(answeredAgent([...answered, { role: 'agent' }, { role: 'ai' }]), false, 'Alex\'s words are not the client\'s')
  assert.equal(answeredAgent(alex), false, 'no agent at all')
  assert.equal(answeredAgent([]), false)
})

test('after an agent: asked as the client closes the chat or leaves the page, the agent unanswered', () => {
  assert.equal(mayAsk(unanswered, 'closed', free), true)
  assert.equal(mayAsk(unanswered, 'hidden', free), true)
  // Rule 5, the clients it leaves alone: a quiet page (they may be waiting for the agent), one who answered.
  assert.equal(mayAsk(unanswered, 'idle', free), false)
  assert.equal(mayAsk(answered, 'closed', free), false)
  assert.equal(mayAsk(answered, 'hidden', free), false)
  // The agent's hello to a visitor who never wrote ("On the site now"): nothing to ask after.
  assert.equal(mayAsk([{ role: 'agent' }], 'closed', free), false)
})

test('with Alex alone, every way of leaving asks, as before', () => {
  for (const why of ['closed', 'hidden', 'idle']) assert.equal(mayAsk(alex, why, free), true)
  assert.equal(mayAsk([{ role: 'ai' }], 'closed', free), false, 'the client never wrote')
})

test('the channel field: empty and the chat closed or the tab gone is leaving; typed in, or on a quiet page, it waits', () => {
  assert.equal(mayAsk(alex, 'closed', { busy: false, field: 'empty' }), true)
  assert.equal(mayAsk(alex, 'hidden', { busy: false, field: 'empty' }), true)
  assert.equal(mayAsk(alex, 'idle', { busy: false, field: 'empty' }), false)
  for (const why of ['closed', 'hidden', 'idle']) {
    assert.equal(mayAsk(alex, why, { busy: false, field: 'typed' }), false)
    assert.equal(mayAsk(alex, why, { busy: true, field: 'none' }), false)
  }
})

test('the server\'s "not yet" leaves the tab its ask; a final answer spends it', () => {
  for (const why of ['just_asked', 'answered_agent', 'agent_wrote', 'never_wrote']) assert.ok(NOT_YET.has(why))
  for (const why of ['has_contact', 'closed', 'already_said', 'no_conversation', 'not_saved', 'unread']) {
    assert.ok(!NOT_YET.has(why))
  }
  assert.equal(NOT_YET_WAIT_MS, 30_000)
  assert.match(chat, /if \(out && !out\.show && out\.why && NOT_YET\.has\(out\.why\)\) \{\s+\/\/ Not now, maybe at the next leave: the tab's one ask is not spent on it\.\s+ssSet\(key, null\)\s+notYetUntilRef\.current = Date\.now\(\) \+ NOT_YET_WAIT_MS/)
  assert.match(chat, /if \(why === 'idle' && Date\.now\(\) < notYetUntilRef\.current\) return/)
})

test('the ask says how the client left, keeps the page alive for it when they leave, and is measured', async () => {
  assert.match(chat, /void askBeforeYouGo\(apiUrl, cid, why !== 'idle', why\)/)
  assert.match(chat, /posthog\?\.capture\('chat_before_you_go', \{ tunnel, why, \.\.\.\(afterAgent \? \{ after_agent: true \} : \{\}\) \}\)/)
  const realFetch = globalThis.fetch
  let asked = ''
  let kept = null
  globalThis.fetch = async (url, init) => {
    asked = String(url)
    kept = init?.keepalive
    return new Response(JSON.stringify({ show: false, why: 'just_asked' }), { status: 200 })
  }
  try {
    assert.deepEqual(await askBeforeYouGo('https://api.test', 'conv-1', true, 'closed'), { show: false, why: 'just_asked' })
    assert.equal(asked, 'https://api.test/api/chat/session/conv-1/before-you-go?why=closed')
    assert.equal(kept, true)
    await askBeforeYouGo('https://api.test', 'conv-1')
    assert.equal(asked, 'https://api.test/api/chat/session/conv-1/before-you-go', 'no word, no query')
  } finally {
    globalThis.fetch = realFetch
  }
})
