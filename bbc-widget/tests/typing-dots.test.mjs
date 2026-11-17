// "Alex is typing" stayed on screen under Alex's finished reply (17 Nov 2026).
// The server pushes the reply's stream_end over SSE and only then answers the
// POST with {streaming: true}. When the SSE frame won the race, the answer
// marked the reply as streaming again after it was already in, and nothing
// cleared it: the 15 s safety net watched `sending` alone, already off.
// Static source-contract tests — zero dependencies (node --test).

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

test('every send takes its own number before anything is shown or posted', () => {
  const seq = send.indexOf('const seq = ++sendSeqRef.current')
  assert.ok(seq > -1, 'the send numbers itself')
  assert.ok(seq < send.indexOf('setSending(true)'), 'before the dots go on')
  assert.ok(seq < send.indexOf('apiFetch('), 'before the first request')
})

test('a stream_end marks the latest send as answered', () => {
  const end = between("if (parsed.event === 'stream_end')", 'return\n')
  assert.match(end, /streamEndSeqRef\.current = sendSeqRef\.current/)
})

test('a reply polled while SSE is down counts as answered too', () => {
  const polled = between('if (sawAi) {', 'return next')
  assert.match(polled, /streamEndSeqRef\.current = sendSeqRef\.current/)
})

test('the POST answer marks the reply streaming only while its stream_end has not come in', () => {
  const branch = between('} else if (data.streaming) {', "} else if (data.message && data.type !== 'queued')")
  const gate = branch.indexOf('streamEndSeqRef.current < seq')
  assert.ok(gate > -1, 'the answer checks the stream did not end first')
  assert.ok(gate < branch.indexOf('setIsStreaming(true)'), 'before it marks the reply streaming')
})

test('a late answer to an earlier send never touches the next one', () => {
  const caught = send.slice(send.indexOf('} catch {'), send.indexOf('} finally {'))
  const latest = caught.indexOf('if (seq === sendSeqRef.current) {')
  assert.ok(latest > -1 && latest < caught.indexOf('setIsStreaming(false)'), 'a failed earlier send leaves the stream alone')
  const fin = send.slice(send.indexOf('} finally {'))
  assert.match(fin, /if \(!isStreamingRef\.current && seq === sendSeqRef\.current\) \{\s*setSending\(false\)/, 'only the latest send unlocks')
})

test('the 15 s safety net also clears a stream left on with sending already off', () => {
  const net = between('// Safety net', 'const notifySessionClose')
  assert.match(net, /if \(!sending && !isStreaming\) return/)
  assert.match(net, /setIsStreaming\(false\)/)
  assert.match(net, /\}, \[sending, isStreaming\]\)/)
})
