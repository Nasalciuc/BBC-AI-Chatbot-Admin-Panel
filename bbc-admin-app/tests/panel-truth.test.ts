// Panel truth: truthful team labels, combobox contract. (The live-state
// matrix went with the old team dashboard; Team & shifts has its own, pinned
// in tests/team-view.test.ts.)
// Runs with: node --experimental-strip-types --test tests/panel-truth.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { teamLabel } from '../src/features/users/components/team-label.ts'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')

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

