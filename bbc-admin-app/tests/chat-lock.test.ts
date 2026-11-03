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
  assert.match(d, /permissions\.canManageTeams && \(/)
  assert.match(d, /unlockConversation\(conversationId\)/)
})

test('a locked push asks first, and a click is never the confirmation', () => {
  const d = read('src/features/chats/detail.tsx')
  const l = read('src/features/leads/index.tsx')
  assert.doesNotMatch(d, /onClick=\{handleMarkLeadCreated\}/)
  assert.match(d, /onClick=\{\(\) => \{ void handleMarkLeadCreated\(\) \}\}/)
  assert.match(l, /onClick=\{\(\) => handlePushToCrm\(lead\)\}/)
  for (const src of [d, l]) {
    assert.match(src, /isLockedPushRefusal\((e|err)\)/)
    assert.match(src, /label: 'Push anyway'/)
  }
  assert.match(read('src/lib/api.ts'), /\?confirm_locked=true/)
})
