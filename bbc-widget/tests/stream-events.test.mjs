// The widget's SSE stream carries more than messages: the server echoes the
// visitor's own typing ({event:'typing'}) and presence events on the same
// conversation stream. 4 Nov 2026: one of those, taken as a message with no
// id or text, crashed the redesigned render and the send button never lit up
// in production. Static source-contract tests — zero dependencies (node --test).

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const chatWindow = readFileSync(join(SRC, 'ChatWindow.tsx'), 'utf8')
const richText = readFileSync(join(SRC, 'richText.tsx'), 'utf8')

const GUARD = "if (parsed.event || typeof parsed.id !== 'string' || typeof parsed.content !== 'string') return"

test('non-message stream events are dropped before anything is appended', () => {
  const streamEnd = chatWindow.indexOf("if (parsed.event === 'stream_end')")
  const onError = chatWindow.indexOf('source.onerror')
  const guard = chatWindow.indexOf(GUARD)
  assert.ok(guard > -1, 'the generic SSE branch must drop events that are not message rows')
  // After the stream_end block (which returns), inside onmessage, before onerror.
  assert.ok(streamEnd > -1 && guard > streamEnd && guard < onError, 'guard sits in the generic branch')
  const generic = chatWindow.slice(guard, onError)
  assert.ok(generic.indexOf('const msg = parsed as Message') > 0, 'the generic message is built after the guard')
  assert.ok(generic.indexOf('setMessages') > 0, 'messages are appended only after the guard')
  assert.ok(generic.indexOf('lastMsgTime') > 0, 'the poll cursor moves only after the guard')
})

test('the render draws only rows with an id and text', () => {
  assert.match(chatWindow, /typeof m\.id === 'string' && typeof m\.content === 'string'/)
})

test('richText survives a missing text', () => {
  assert.match(richText, /if \(typeof text !== 'string' \|\| !text\) return \[\]/)
})

test('a stream closed for good by an HTTP error falls back to polling at once, and tries SSE again', () => {
  // CodeRabbit #228: an early `return` for a CLOSED source ran first, so the
  // give-up path never saw it: no retry, and the stream stayed marked up.
  const onError = chatWindow.slice(chatWindow.indexOf('source.onerror = () => {'), chatWindow.indexOf('catchUpPoll()  // instant load'))
  assert.equal((onError.match(/EventSource\.CLOSED/g) ?? []).length, 1, 'one check for a closed stream')
  assert.doesNotMatch(onError, /readyState === EventSource\.CLOSED\) \{\s*source = null/, 'no early return before the give-up path')
  const closed = onError.indexOf('const closedForGood = source?.readyState === EventSource.CLOSED')
  assert.ok(closed > onError.indexOf('sseUpRef.current = false'), 'the stream is marked down first')
  const giveUp = onError.slice(onError.indexOf('if (closedForGood || errorCount >= MAX_ERRORS) {'))
  assert.ok(giveUp.indexOf('startPolling()') > 0, 'polls at once')
  assert.ok(giveUp.indexOf('sseRetryDelay(retryAttempt++)') > giveUp.indexOf('startPolling()'), 'and tries SSE again, backing off')
})
