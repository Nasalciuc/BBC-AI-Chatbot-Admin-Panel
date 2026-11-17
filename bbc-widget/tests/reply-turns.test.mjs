// Which send a reply belongs to (src/replyTurns.ts). Each test replays the order in which the widget
// hears things: its own sends, the replies that come in (a stream_end, or an AI row the poll brings
// while SSE is down) and each send's POST answer. "streams" is what the answer may do: mark the
// reply streaming (the typing dots) only while its send still waits for that reply.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { createReplyTurns } from '../src/replyTurns.ts'

test('17 Nov: the reply comes in before the POST answer — the answer must not mark it streaming', () => {
  const t = createReplyTurns()
  const one = t.start()
  t.replyCameIn()
  assert.equal(t.isOpen(one), false)
})

test('the usual order: the answer first, then the reply', () => {
  const t = createReplyTurns()
  const one = t.start()
  assert.equal(t.isOpen(one), true)   // the dots go on
  t.replyCameIn()
  assert.equal(t.isOpen(one), false)
})

test('a reply the poll brings back late closes the send it belongs to, not the newer one (CodeRabbit #264)', () => {
  const t = createReplyTurns()
  const one = t.start()               // its stream_end is lost: SSE was down
  const two = t.start()               // the 15 s net unlocked, the queue sent the next message
  t.replyCameIn()                     // the poll brings send 1's reply
  assert.equal(t.isOpen(one), false)
  assert.equal(t.isOpen(two), true)   // send 2's answer still marks its own reply streaming
})

test('a stream_end that comes late closes its own send, not the newer one (CodeRabbit #264)', () => {
  const t = createReplyTurns()
  const one = t.start()
  assert.equal(t.isOpen(one), true)   // the answer came first
  const two = t.start()               // the net unlocked before the stream_end came
  t.replyCameIn()                     // send 1's stream_end, late
  assert.equal(t.isOpen(two), true)
  t.replyCameIn()                     // send 2's
  assert.equal(t.isOpen(two), false)
})

test('sends that stream nothing (a person has the chat) do not take a later reply', () => {
  const t = createReplyTurns()
  for (let i = 0; i < 3; i++) t.over(t.start())   // three answers with no stream
  t.replyCameIn()                                  // Alex again once the chat comes back to him
  const next = t.start()
  t.replyCameIn()                                  // its reply, before its answer
  assert.equal(t.isOpen(next), false)
})

test('a reply with no send waiting (an opening, a second tab) closes nothing', () => {
  const t = createReplyTurns()
  t.replyCameIn()
  const one = t.start()
  assert.equal(t.isOpen(one), true)
})

test('a send that failed waits for nothing', () => {
  const t = createReplyTurns()
  const one = t.start()
  t.over(one)
  assert.equal(t.isOpen(one), false)
})

test('only the latest send is the latest', () => {
  const t = createReplyTurns()
  const one = t.start()
  assert.equal(t.isLatest(one), true)
  const two = t.start()
  assert.equal(t.isLatest(one), false)
  assert.equal(t.isLatest(two), true)
})
