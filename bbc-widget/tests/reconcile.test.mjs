/**
 * The vladimirtechtest race: the client's own message vanished from the UI
 * at the exact moment the human agent joined — the poll's temp-cleanup
 * removed the optimistic bubble, and the agent-joined burst had advanced
 * the ?after= cursor past the user row's timestamp, so no batch ever
 * brought it back. Pins the new contract in src/reconcile.ts:
 * send-response promotes, poll only expires stale temps, server rows
 * replace their local twins.
 *
 * Plain .mjs + strip-types import: matches first-contact.test.mjs, the
 * widget's zero-dep test pattern.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'

const { promoteTemp, reconcileBatch, TEMP_TTL_MS } = await import(
  '../src/reconcile.ts'
)

const NOW = 1_755_000_000_000
const msg = (id, role, content, created_at = '2026-08-12T10:00:00Z') => ({
  id, role, content, created_at,
})

test('handoff scenario end-to-end: user message survives the agent burst', () => {
  // Client sends → optimistic temp renders.
  let messages = [
    msg('ai-1', 'ai', 'Welcome!'),
    msg(`temp-${NOW}`, 'user', 'I want LAX to SYD'),
  ]
  // POST /api/chat returns 200 → the send path promotes the temp.
  messages = promoteTemp(messages, `temp-${NOW}`, NOW + 100)

  // Agent-joined burst arrives WITHOUT the user row (the ?after= cursor
  // already skipped it). Old code: temp removed, message gone forever.
  const burst = [
    msg('sys-1', 'system', 'Max joined the conversation'),
    msg('agent-1', 'agent', 'Hi, Max here — picking this up.'),
  ]
  const r1 = reconcileBatch(messages, burst, NOW + 3_000)
  const bodies = r1.next.map((m) => m.content)
  assert.ok(bodies.includes('I want LAX to SYD'), 'client message must survive')
  assert.ok(bodies.includes('Hi, Max here — picking this up.'))

  // A later batch DOES carry the server row → it replaces the local twin.
  const later = [msg('uuid-77', 'user', 'I want LAX to SYD', '2026-08-12T10:00:01Z')]
  const r2 = reconcileBatch(r1.next, later, NOW + 6_000)
  const userRows = r2.next.filter((m) => m.content === 'I want LAX to SYD')
  assert.equal(userRows.length, 1, 'server row replaces the twin — no duplicate')
  assert.equal(userRows[0].id, 'uuid-77', 'server id wins')
})

test('poll never removes a fresh in-flight temp', () => {
  const prev = [msg(`temp-${NOW}`, 'user', 'hello')]
  const r = reconcileBatch(prev, [msg('ai-2', 'ai', 'hi!')], NOW + 2_000)
  assert.ok(r.next.some((m) => m.id === `temp-${NOW}`))
})

test('failed send: stale temp expires after TTL (error bubble already shown)', () => {
  const prev = [msg(`temp-${NOW}`, 'user', 'lost one')]
  const r = reconcileBatch(prev, [], NOW + TEMP_TTL_MS + 1)
  assert.equal(r.next.length, 0)
  assert.ok(r.changed)
})

test('sent-* is immune to expiry — the 200 was the receipt', () => {
  const prev = [msg(`sent-${NOW}`, 'user', 'confirmed one')]
  const r = reconcileBatch(prev, [], NOW + TEMP_TTL_MS * 50)
  assert.equal(r.next.length, 1)
})

test('no-change batch keeps the previous array reference (no re-render)', () => {
  const prev = [msg('ai-1', 'ai', 'Welcome!')]
  const r = reconcileBatch(prev, [msg('ai-1', 'ai', 'Welcome!')], NOW)
  assert.equal(r.next, prev)
  assert.equal(r.changed, false)
})

test('sawAi fires only on genuinely new ai rows', () => {
  const prev = [msg('ai-1', 'ai', 'Welcome!')]
  assert.equal(reconcileBatch(prev, [msg('ai-2', 'ai', 'More')], NOW).sawAi, true)
  assert.equal(reconcileBatch(prev, [msg('sys-9', 'system', 'x')], NOW).sawAi, false)
})

test("dedup never touches other roles or other users' different texts", () => {
  const prev = [
    msg(`sent-${NOW}`, 'user', 'first thing'),
    msg('agent-3', 'agent', 'first thing'), // same text, agent — untouched
  ]
  const r = reconcileBatch(prev, [msg('uuid-9', 'user', 'second thing')], NOW)
  assert.equal(r.next.filter((m) => m.content === 'first thing').length, 2)
})
