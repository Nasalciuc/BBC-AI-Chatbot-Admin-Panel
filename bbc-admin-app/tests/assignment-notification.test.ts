/**
 * Wave 4 PR 3 — assignments ring the assigned operator, ≤5s.
 *
 * The 30s silent-handoff window (fall_back_to_ai) means an unnoticed
 * assignment becomes an AI takeover. Source contracts pin: the poll that
 * detects assignments stays ≤5s, the toast names the chat and offers Open,
 * the browser Notification never prompts from the alert path, and the list
 * highlights exactly the rows the heartbeat says need attention.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p: string) => readFileSync(join(root, p), 'utf-8')

test('assignment poll interval is 5s or faster', () => {
  const src = read('src/hooks/use-heartbeat.ts')
  const m = src.match(/HEARTBEAT_INTERVAL_MS = (\d[\d_]*)/)
  assert.ok(m, 'HEARTBEAT_INTERVAL_MS must exist')
  const ms = Number(m![1].replace(/_/g, ''))
  assert.ok(ms <= 5_000, `assigned-to-me poll must be <=5s, got ${ms}ms`)
})

test('my_active list poll matches the <=5s requirement', () => {
  const src = read('src/features/chats/index.tsx')
  // Dormant turns the poll OFF for a panel nobody is looking at; when it IS
  // being looked at the cadence is unchanged at 5s.
  assert.match(src, /refetchInterval: dormant \? false : 5_000/)
})

test('toast names the chat and offers Open', () => {
  const src = read('src/hooks/use-heartbeat.ts')
  assert.match(src, /New chat assigned/)
  assert.match(src, /label: 'Open'/)
  assert.match(src, /highlight=\$\{id\}/)
})

test('one toast per conversation per episode, cleared when alerts stop', () => {
  const src = read('src/hooks/use-heartbeat.ts')
  assert.match(src, /toasted\.current\.has\(id\)/)
  assert.match(src, /toasted\.current\.clear\(\)/)
})

test('alert path never prompts for Notification permission', () => {
  const src = read('src/lib/notify-assignment.ts')
  // notifyAssignment must gate on already-granted…
  assert.match(src, /Notification\.permission === 'granted'/)
  // …and requestPermission must live ONLY in the explicit unlock helper.
  const notifyBody = src.slice(
    src.indexOf('export function notifyAssignment'),
    src.indexOf('export function stopAssignmentAlerts'),
  )
  assert.ok(
    !notifyBody.includes('requestPermission'),
    'notifyAssignment must never prompt — permission is requested only by requestNotifyPermission',
  )
})

test('list rows highlight the attention set', () => {
  const list = read('src/features/chats/index.tsx')
  assert.match(list, /useAttentionStore/)
  assert.match(list, /attentionIds\.includes\(conv\.id\)/)
  const hook = read('src/hooks/use-heartbeat.ts')
  assert.match(hook, /setAttentionIds\(\[\.\.\.needsAttention\]\)/)
})

test('only the assigned operator is polled — others stay silent', () => {
  const src = read('src/hooks/use-heartbeat.ts')
  assert.match(src, /assigned_to: 'me'/)
  assert.match(src, /canReceiveAssignNotifications/)
})
