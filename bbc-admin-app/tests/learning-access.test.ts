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
