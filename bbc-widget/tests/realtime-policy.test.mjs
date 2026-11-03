/**
 * PR-10 — fewer requests from the widget: typing at most once a second and
 * only on change; operator typing on SSE before the generic message branch;
 * the poll only while SSE is down, with SSE retried on a capped backoff.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  shouldSendTyping, sseRetryDelay, TYPING_MIN_GAP_MS, SSE_RETRY_MAX_MS,
} from '../src/realtime-policy.ts'

const chatWindow = readFileSync(
  join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'ChatWindow.tsx'),
  'utf8',
)

test('typing: unchanged text never sends; changed text waits a second', () => {
  assert.equal(shouldSendTyping('hello', 'hello', 0, 10_000), false)
  assert.equal(shouldSendTyping('hello!', 'hello', 1_000, 1_000 + TYPING_MIN_GAP_MS - 1), false)
  assert.equal(shouldSendTyping('hello!', 'hello', 1_000, 1_000 + TYPING_MIN_GAP_MS), true)
})

test('typing: a 10 s burst of keystrokes costs at most ~10 requests, not 50', () => {
  let lastAt = -Infinity, lastText = '', sent = 0, text = ''
  for (let t = 0; t < 10_000; t += 200) {
    text += 'a'
    if (shouldSendTyping(text, lastText, lastAt, t)) { sent++; lastAt = t; lastText = text }
  }
  assert.ok(sent <= 10, `sent ${sent}`)
})

test('SSE retry backs off and caps at 60 s', () => {
  assert.equal(sseRetryDelay(0), 5_000)
  assert.equal(sseRetryDelay(1), 10_000)
  assert.equal(sseRetryDelay(2), 20_000)
  assert.equal(sseRetryDelay(10), SSE_RETRY_MAX_MS)
})

test('the typing frame is handled before the branch that renders messages', () => {
  const typingAt = chatWindow.indexOf("if (parsed.event === 'typing')")
  const chunkAt = chatWindow.indexOf("if (parsed.event === 'stream_chunk')")
  const genericAt = chatWindow.indexOf("if (msg.role === 'user') return")
  assert.ok(typingAt > 0 && typingAt < chunkAt && chunkAt < genericAt)
})

test('the widget asks for v2 frames and the poll stands down while SSE is up', () => {
  assert.match(chatWindow, /&v=2`\)/)
  assert.match(chatWindow, /if \(sseUpRef\.current\) return/)
  assert.match(chatWindow, /stopPolling\(\)/)
  assert.match(chatWindow, /sseRetryDelay\(retryAttempt\+\+\)/)
})
