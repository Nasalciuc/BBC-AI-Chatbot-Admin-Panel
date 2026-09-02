/**
 * The widget already renders the visitor's own message under a temp- id.
 * The server now fans that row out (UUID) so the operator's panel sees it.
 * Taking it from SSE would show it twice — the poll still delivers it to
 * a second tab, which is why lastMsgTime must not jump over a skipped user.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const chatWindow = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'ChatWindow.tsx'),
  'utf8',
)

function genericSseBranch() {
  const streamEnd = chatWindow.indexOf("if (parsed.event === 'stream_end')")
  const insideEnd = chatWindow.indexOf('const msg = parsed as Message', streamEnd + 1)
  const generic = chatWindow.indexOf('const msg = parsed as Message', insideEnd + 1)
  const end = chatWindow.indexOf('source.onerror', generic)
  assert.ok(generic > insideEnd && end > generic, 'generic SSE branch not found')
  return chatWindow.slice(generic, end)
}

function applyGeneric(parsed, messages, lastMsgTime) {
  const msg = parsed
  if (msg.role === 'user') return { messages, lastMsgTime }
  if (messages.some((m) => m.id === msg.id)) return { messages, lastMsgTime }
  const next = [...messages, msg]
  return { messages: next, lastMsgTime: msg.created_at ? msg.created_at : lastMsgTime }
}

test('SSE user frame leaves messages and lastMsgTime unchanged; agent is appended', () => {
  const generic = genericSseBranch()
  assert.match(generic, /if \(msg\.role === 'user'\) return/)
  const skipAt = generic.indexOf("if (msg.role === 'user') return")
  assert.ok(skipAt < generic.indexOf('setMessages'), 'skip must run before append')
  assert.ok(skipAt < generic.indexOf('lastMsgTime'), 'skip must not advance the poll cursor')

  let state = {
    messages: [{ id: 'temp-1', role: 'user', content: 'hi' }],
    lastMsgTime: 't0',
  }
  state = applyGeneric(
    { id: 'uuid', role: 'user', content: 'hi', created_at: 't1' },
    state.messages,
    state.lastMsgTime,
  )
  assert.deepEqual(state.messages, [{ id: 'temp-1', role: 'user', content: 'hi' }])
  assert.equal(state.lastMsgTime, 't0')

  state = applyGeneric(
    { id: 'a1', role: 'agent', content: 'hello', created_at: 't2' },
    state.messages,
    state.lastMsgTime,
  )
  assert.equal(state.messages.length, 2)
  assert.equal(state.messages[1].role, 'agent')
  assert.equal(state.messages[1].id, 'a1')
  assert.equal(state.lastMsgTime, 't2')
})

test('stream_end still takes the row — the user skip is only on the generic branch', () => {
  const streamEnd = chatWindow.indexOf("if (parsed.event === 'stream_end')")
  const insideEnd = chatWindow.indexOf('const msg = parsed as Message', streamEnd + 1)
  const generic = chatWindow.indexOf('const msg = parsed as Message', insideEnd + 1)
  const endBlock = chatWindow.slice(streamEnd, generic)
  assert.doesNotMatch(endBlock, /msg\.role === 'user'/)
})
