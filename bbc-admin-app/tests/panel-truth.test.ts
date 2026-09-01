// Panel truth: live-state matrix, truthful team labels, combobox contract.
// Runs with: node --experimental-strip-types --test tests/panel-truth.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describeLiveAgent, groupByRole, lastSeenLabel } from '../src/features/dashboard/components/live-state.ts'
import { teamLabel } from '../src/features/users/components/team-label.ts'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const NOW = new Date('2026-08-08T12:00:00Z')

const agent = (over: Record<string, unknown>) => ({
  id: 'u1', name: 'Emma', role: 'sales', is_ready: false,
  is_online: false, last_seen: null, ...over,
}) as never

test('ready = online + is_ready → green', () => {
  const d = describeLiveAgent(agent({ is_online: true, is_ready: true }), NOW)
  assert.equal(d.state, 'ready')
  assert.equal(d.dot, 'bg-emerald-500')
  assert.equal(d.detail, null)
})

test('online but not ready → neutral with detail', () => {
  const d = describeLiveAgent(agent({ is_online: true }), NOW)
  assert.equal(d.state, 'online')
  assert.equal(d.detail, 'online, not ready')
})

test('offline → gray with last-seen age', () => {
  const seen = new Date(NOW.getTime() - 12 * 60000).toISOString()
  const d = describeLiveAgent(agent({ last_seen: seen }), NOW)
  assert.equal(d.state, 'offline')
  assert.equal(d.detail, 'last seen 12m ago')
})

test('offline without timestamp → plain offline', () => {
  assert.equal(describeLiveAgent(agent({}), NOW).detail, 'offline')
})

test('lastSeenLabel handles hours', () => {
  const seen = new Date(NOW.getTime() - 80 * 60000).toISOString()
  assert.equal(lastSeenLabel(seen, NOW), 'last seen 1h 20m ago')
})

test('groupByRole orders sales, support, supervisor', () => {
  const groups = groupByRole([
    agent({ id: 'a', role: 'supervisor' }),
    agent({ id: 'b', role: 'sales' }),
    agent({ id: 'c', role: 'support' }),
  ])
  assert.deepEqual(groups.map(([r]) => r), ['sales', 'support', 'supervisor'])
})

// ── Team ghost regression: no badge without a real team_id relationship ──

test('NULL team_id → no label (renders "— no team")', () => {
  assert.equal(teamLabel(null, [{ id: 't1', name: 'Test group' }]), null)
  assert.equal(teamLabel(undefined, [{ id: 't1', name: 'Test group' }]), null)
})

test('team_id resolves only against a real team row', () => {
  const teams = [{ id: 't1', name: 'Test group' }]
  assert.equal(teamLabel('t1', teams), 'Test group')
  assert.equal(teamLabel('ghost-id', teams), null) // dangling id → no badge
})

test('TeamCell renders the muted no-team text for missing labels', () => {
  const cell = readFileSync(join(SRC, 'features/users/components/team-cell.tsx'), 'utf8')
  assert.match(cell, /— no team/)
  assert.match(cell, /teamLabel\(/)
})

// ── Operator combobox contract ──

test('reassign panel is a real combobox: filter input + keyboard-capable Command', () => {
  const panel = readFileSync(join(SRC, 'components/reassign-panel.tsx'), 'utf8')
  assert.match(panel, /CommandInput/)
  assert.match(panel, /Search name or email/)
  // Filter value covers BOTH name and email.
  assert.match(panel, /value=\{`\$\{u\.name \?\? ''\} \$\{u\.email \?\? ''\}`\}/)
  assert.match(panel, /role='combobox'/)
})

test('team live card polls every 30s — and not at all when nobody is looking', () => {
  const card = readFileSync(join(SRC, 'features/dashboard/components/team-live-card.tsx'), 'utf8')
  assert.match(card, /refetchInterval: dormant \? false : 30_000/)
})
