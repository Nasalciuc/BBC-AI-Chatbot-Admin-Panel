// What Users & access says — pinned on features/users/view.ts.
// Runs with: node --experimental-strip-types --test tests/users-view.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import type { PanelUser } from '../src/lib/types.ts'
import {
  CAPABILITIES,
  accessSentence,
  canDo,
  cannotDo,
  emailTypo,
  lastActive,
  logWhen,
  roleChange,
  statusOf,
  tunnelFor,
} from '../src/features/users/view.ts'

const NOW = Date.parse('2026-11-06T12:00:00Z')
const H = 3_600_000

function user(over: Partial<PanelUser> = {}): PanelUser {
  return {
    id: 'u1', email: 'victor.rusu@buybusinessclass.com', name: 'Victor Rusu', role: 'sales', tunnel_scope: 'sales',
    avatar_url: null, is_active: true, last_seen_at: new Date(NOW - 5 * 60_000).toISOString(), phone: null, team_id: null,
    created_at: '2026-09-01T00:00:00Z', ...over,
  }
}

test('status: active, invite pending or expired, deactivated', () => {
  assert.equal(statusOf(user(), NOW).label, 'Active')
  const pending = statusOf(user({ last_seen_at: null, invite: { expires_at: new Date(NOW + 45 * H).toISOString(), used_at: null, sent_at: null, sent_by: null } }), NOW)
  assert.deepEqual([pending.label, pending.sub], ['Invite pending', 'Expires in 45 h'])
  const expired = statusOf(user({ last_seen_at: null, invite: { expires_at: new Date(NOW - 2 * H).toISOString(), used_at: null, sent_at: null, sent_by: null } }), NOW)
  assert.deepEqual([expired.label, expired.sub], ['Invite expired', 'Expired 2 h ago'])
  // Someone who signs in from the CRM has an old unused invite: still Active.
  assert.equal(statusOf(user({ invite: { expires_at: '2026-08-01T00:00:00Z', used_at: null, sent_at: null, sent_by: null } }), NOW).label, 'Active')
  assert.equal(statusOf(user({ is_active: false }), NOW).label, 'Deactivated')
  assert.equal(statusOf(user({ last_seen_at: null, invite: null }), NOW).label, 'Not signed in yet')
})

test('a used invite with no sign-in since is not an expired invite', () => {
  const used = (expires: number) =>
    statusOf(user({ last_seen_at: null, invite: { expires_at: new Date(expires).toISOString(), used_at: new Date(NOW - 50 * H).toISOString(), sent_at: null, sent_by: null } }), NOW)
  // The password is set; "Invite expired · send a new one" would be wrong either way.
  assert.equal(used(NOW - 2 * H).key, 'never')
  assert.equal(used(NOW + 2 * H).key, 'never')
  assert.equal(used(NOW - 2 * H).label, 'Not signed in yet')
})

test('the person panel starts clean for each person and says when the log failed', async () => {
  const { readFileSync } = await import('node:fs')
  const panel = readFileSync(new URL('../src/features/users/components/person-panel.tsx', import.meta.url), 'utf8')
  // A role picked (not saved) for one person is never offered for the next one.
  assert.match(panel, /if \(\(user\?\.id \?\? null\) !== shownId\) \{\s*setShownId\(user\?\.id \?\? null\)\s*setPendingRole\(null\)\s*setGiveTo\(''\)\s*setConfirmOff\(false\)/)
  // The changes to someone's access are shown to the people who can read them, and a failed read says so.
  assert.match(panel, /\{canManage && \(\s*<section aria-label='Changes to their access'/)
  assert.match(panel, /logFailed && log === undefined \?/)
  assert.match(panel, /None among the latest \$\{logLimit\} changes/)
})

test('last active reads like a person would say it', () => {
  assert.equal(lastActive(new Date(NOW - 3 * 60_000).toISOString(), NOW, 'UTC'), 'Now')
  assert.equal(lastActive(new Date(NOW - 20 * 60_000).toISOString(), NOW, 'UTC'), '20 min ago')
  assert.equal(lastActive('2026-11-06T09:20:00Z', NOW, 'UTC'), 'Today 09:20')
  assert.equal(lastActive('2026-11-05T09:20:00Z', NOW, 'UTC'), 'Yesterday')
  assert.equal(lastActive('2026-10-12T09:20:00Z', NOW, 'UTC'), '12 Oct')
  assert.equal(lastActive(null, NOW), 'Never signed in')
  assert.equal(logWhen('2026-11-06T11:58:00Z', NOW, 'UTC'), 'Just now')
  assert.equal(logWhen('2026-11-06T09:20:00Z', NOW, 'UTC'), 'Today 09:20')
  assert.equal(logWhen('2026-11-05T18:20:00Z', NOW, 'UTC'), 'Yesterday 18:20')
  assert.equal(logWhen('2026-11-02T10:15:00Z', NOW, 'UTC'), '2 Nov, 10:15')
})

test('the roles table matches what the endpoints allow', () => {
  const cell = (label: string, role: string) => CAPABILITIES.find((c) => c.label === label)!.cells[role as 'sales']
  // Claim gate: sales, support, owner, admin, dev.
  assert.deepEqual(['sales', 'support', 'supervisor', 'project_manager', 'qa', 'admin', 'dev', 'owner'].map((r) => cell('Answer chats', r)),
    ['Sales', 'Support', 'n', 'n', 'n', 'y', 'y', 'y'])
  // mark-crm-created: owner, admin, dev, sales.
  assert.equal(cell('Send a lead to the CRM', 'sales'), 'Their chats')
  assert.equal(cell('Send a lead to the CRM', 'qa'), 'n')
  // GET /api/leads: dev gets 403.
  assert.equal(cell('See leads', 'dev'), 'n')
  // Only an owner changes owners — admin and dev both.
  assert.equal(cell('Invite people and change roles', 'admin'), 'Not owners')
  assert.equal(cell('Invite people and change roles', 'dev'), 'Not owners')
  assert.equal(tunnelFor('sales'), 'sales')
  assert.equal(tunnelFor('qa'), 'all')
})

test('what a role can and can’t do, and what a change does', () => {
  assert.deepEqual(canDo('sales').map((c) => c.label), [
    'Answer chats', 'See chats', 'See a visitor’s phone and e-mail', 'Send a lead to the CRM', 'Knowledge base', 'Settings',
  ])
  assert.ok(cannotDo('sales').includes('see leads'))
  const d = roleChange('sales', 'qa')
  assert.ok(d.gains.includes('See leads'))
  assert.ok(d.losses.includes('Answer chats'))
})

test('an e-mail typo is caught before the invite bounces', () => {
  assert.deepEqual(emailTypo('daria.popa@buybusinesclass.com'), {
    domain: 'buybusinesclass.com', fixed: 'buybusinessclass.com', suggestion: 'daria.popa@buybusinessclass.com',
  })
  assert.equal(emailTypo('liam.ortiz@gmial.com')?.fixed, 'gmail.com')
  assert.equal(emailTypo('ok@buybusinessclass.com'), null)
})

test('the access log in sentences', () => {
  const base = { at: '2026-11-05T18:20:00Z', actor_id: 'a', actor_name: 'Alex Moore', target_id: 'e', target_name: 'Elena Vasile' }
  assert.equal(accessSentence({ ...base, kind: 'role', old: 'supervisor', new: 'qa' }).text, 'changed Elena Vasile’s role: Supervisor → QA.')
  assert.equal(accessSentence({ ...base, kind: 'invited', role: 'sales' }).text, 'invited Elena Vasile as Sales agent.')
  assert.equal(accessSentence({ ...base, kind: 'chats_on' }).text, 'turned on “Receives chats” for Elena Vasile.')
  assert.equal(accessSentence({ ...base, kind: 'deactivated' }).text, 'deactivated Elena Vasile.')
})
