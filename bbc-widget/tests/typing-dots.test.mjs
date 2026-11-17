// "Alex is typing" stayed on screen under Alex's finished reply (17 Nov 2026).
// The server pushes the reply's stream_end over SSE and only then answers the
// POST with {streaming: true}. When the SSE frame won the race, the answer
// marked the reply as streaming again after it was already in, and nothing
// cleared it: the 15 s safety net watched `sending` alone, already off.
// Which send a reply belongs to is replyTurns.ts (tests/reply-turns.test.mjs);
// these check ChatWindow uses it at every step. Static source-contract tests —
// zero dependencies (node --test).

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const chatWindow = readFileSync(join(SRC, 'ChatWindow.tsx'), 'utf8')

const between = (from, to) => {
  const a = chatWindow.indexOf(from)
  const b = chatWindow.indexOf(to, a)
  assert.ok(a > -1 && b > a, `${from} … ${to}`)
  return chatWindow.slice(a, b)
}

const send = between('const sendMessage = async', 'const handleSend = () =>')
// The send's own catch (inline `try { … } catch {}` lines come before it).
const failed = send.indexOf("} catch {\n      posthog?.capture('chat_message_send_failed'")
const caught = send.slice(failed, send.indexOf('} finally {'))

test('one record of sends and replies for the life of the chat', () => {
  assert.match(chatWindow, /const \[turns\] = useState\(createReplyTurns\)/)
  assert.doesNotMatch(chatWindow, /sendSeqRef|streamEndSeqRef/)
})

test('every send takes its own number before anything is shown or posted', () => {
  const seq = send.indexOf('const seq = turns.start()')
  assert.ok(seq > -1, 'the send numbers itself')
  assert.ok(seq < send.indexOf('setSending(true)'), 'before the dots go on')
  assert.ok(seq < send.indexOf('apiFetch('), 'before the first request')
})

test('a stream_end and a reply polled while SSE is down both count as a reply that came in', () => {
  assert.match(between("if (parsed.event === 'stream_end')", 'return\n'), /turns\.replyCameIn\(\)/)
  assert.match(between('if (sawAi) {', 'return next'), /turns\.replyCameIn\(\)/)
})

test('the POST answer marks the reply streaming only while its send still waits for it', () => {
  const branch = between('} else if (data.streaming) {', "} else if (data.message && data.type !== 'queued')")
  const gate = branch.indexOf('streams = Boolean(activeConvId || convId) && turns.isOpen(seq)')
  assert.ok(gate > -1, 'the answer checks the reply did not come in first')
  assert.ok(gate < branch.indexOf('setIsStreaming(true)'), 'before it marks the reply streaming')
})

test('an answer that streams nothing, or a failed send, ends that send\'s turn', () => {
  assert.ok(failed > -1, 'the send has its own catch')
  const success = send.slice(send.indexOf('let streams = false'), failed)
  assert.match(success, /if \(!streams\) turns\.over\(seq\)/)
  assert.match(caught, /turns\.over\(seq\)/)
})

test('a late answer to an earlier send never touches the next one', () => {
  const latest = caught.indexOf('if (turns.isLatest(seq)) {')
  assert.ok(latest > -1 && latest < caught.indexOf('setIsStreaming(false)'), 'a failed earlier send leaves the stream alone')
  const fin = send.slice(send.indexOf('} finally {'))
  assert.match(fin, /if \(!isStreamingRef\.current && turns\.isLatest\(seq\)\) \{\s*setSending\(false\)/, 'only the latest send unlocks')
})

test('the 15 s safety net also clears a stream left on with sending already off', () => {
  const net = between('// Safety net', 'const notifySessionClose')
  assert.match(net, /if \(!sending && !isStreaming\) return/)
  assert.match(net, /setIsStreaming\(false\)/)
  assert.match(net, /\}, \[sending, isStreaming\]\)/)
})
