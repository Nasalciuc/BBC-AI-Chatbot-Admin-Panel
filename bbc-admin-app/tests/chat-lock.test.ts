/**
 * PR-15 — the panel side of the chat lock: the badge with Unlock, and a CRM
 * push of a locked conversation that waits for the operator's yes. The click
 * event must never stand in for that yes.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p: string) => readFileSync(join(root, p), 'utf-8')

test('the lock shows as a badge with Unlock for managers', () => {
  const d = read('src/features/chats/detail.tsx')
  assert.match(d, /Blocked — abuse/)
  // The endpoint is MANAGEMENT (owner, admin, dev): the same set as canManageBans.
  assert.match(d, /!permissions\.canManageBans \? \(/)
  assert.doesNotMatch(d, /permissions\.canManageTeams && \(\s*<button[^>]*unlock/)
  assert.match(d, /unlockConversation\(conversationId\)/)
})

test('others see Unlock disabled, with a lock and a reason (RBAC rule)', () => {
  const d = read('src/features/chats/detail.tsx')
  const start = d.indexOf('!permissions.canManageBans ? (')
  const disabled = d.slice(start, d.indexOf(') : (', start))
  assert.match(disabled, /\bdisabled\b/)
  assert.match(disabled, /<Lock /)
  assert.match(disabled, /title=['"]Only an owner, admin or dev can unlock a conversation['"]/)
  assert.doesNotMatch(disabled, /onClick/)
})

test('a locked push asks first, and a click is never the confirmation', () => {
  // The redesign: the chat's push button lives in its details panel (onPush,
  // called with the click event), the Leads page's in the lead panel.
  const d = read('src/features/chats/detail.tsx')
  const l = read('src/features/leads/components/lead-panel.tsx')
  assert.doesNotMatch(d, /(onClick|onPush)=\{handleMarkLeadCreated\}/)
  assert.match(d, /onPush=\{\(\) => \{ void handleMarkLeadCreated\(\) \}\}/)
  assert.match(l, /onClick=\{\(\) => push\.mutate\(\{ leadId: id \}\)\}/)
  for (const src of [d, l]) {
    assert.match(src, /isLockedPushRefusal\((e|err)\)/)
    assert.match(src, /label: 'Push anyway'/)
  }
  assert.match(read('src/lib/api.ts'), /\?confirm_locked=true/)
})
