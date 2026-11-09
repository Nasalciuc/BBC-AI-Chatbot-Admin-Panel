/**
 * The Learning page is gated on the same roles the backend lets in
 * (CAN_REVIEW_LESSONS = owner/admin/dev). A project manager can manage teams
 * but must not see a Learning link whose every request answers 403.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'

import { getPermissions } from '../src/lib/bbc/hooks.ts'

test('canReviewLearning: owner, admin and dev only', () => {
  for (const role of ['owner', 'admin', 'dev'] as const) {
    assert.equal(getPermissions(role).canReviewLearning, true, role)
  }
  for (const role of ['project_manager', 'supervisor', 'qa', 'sales', 'support'] as const) {
    assert.equal(getPermissions(role).canReviewLearning, false, role)
  }
})

test('a project manager manages teams but is not a learning reviewer', () => {
  const pm = getPermissions('project_manager')
  assert.equal(pm.canManageTeams, true)
  assert.equal(pm.canReviewLearning, false)
})

test('canManageBans matches the backend MANAGEMENT set (owner, admin, dev)', () => {
  for (const role of ['owner', 'admin', 'dev'] as const) {
    assert.equal(getPermissions(role).canManageBans, true, role)
  }
  for (const role of ['project_manager', 'supervisor', 'qa', 'sales', 'support'] as const) {
    assert.equal(getPermissions(role).canManageBans, false, role)
  }
})

// ── CodeRabbit #227 ───────────────────────────────────────────────────────
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p: string) => readFileSync(join(root, p), 'utf-8')

test('a typed /learning URL is refused to who may not review, like the menu', () => {
  const route = read('src/routes/_authenticated/learning/index.tsx')
  assert.match(route, /beforeLoad: \(\) => \{/)
  assert.match(route, /if \(!role \|\| !getPermissions\(role\)\.canReviewLearning\) \{\s*throw redirect\(\{ to: '\/403' \}\)/)
})

test('Activate and Retire wait for the change on its way', () => {
  const page = read('src/features/learning/index.tsx')
  const buttons = page.match(/<button type="button" disabled=\{mutate\.isPending\}[\s\S]*?>(Activate|Retire)<\/button>/g) ?? []
  assert.deepEqual(buttons.map((b) => b.match(/>(\w+)<\/button>$/)?.[1]), ['Activate', 'Retire'])
})

test('the status chips use the palette, not light-only tints', () => {
  const page = read('src/features/learning/index.tsx')
  assert.doesNotMatch(page, /\b(bg|text)-(green|amber)-\d{3}\b/)
  assert.match(page, /active: 'bg-k-e3f6ea text-k-0a6b35'/)
  assert.match(page, /proposed: 'bg-k-fff3e0 text-k-8a4b00'/)
})
