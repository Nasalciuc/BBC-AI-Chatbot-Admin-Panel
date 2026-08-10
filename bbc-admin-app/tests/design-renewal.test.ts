// Design renewal — one header everywhere, KB grown up, de-slopped details.
// Runs with: node --experimental-strip-types --test tests/design-renewal.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const read = (p: string) => readFileSync(join(SRC, p), 'utf8')

const FEATURES = [
  'features/dashboard/index.tsx', 'features/chats/index.tsx',
  'features/leads/index.tsx', 'features/knowledge-base/index.tsx',
  'features/users/index.tsx', 'features/teams/index.tsx',
  'features/settings/index.tsx', 'features/tasks/index.tsx',
  'features/apps/index.tsx',
]

test('ONE header: every page renders HeaderActions, none composes its own cluster', () => {
  for (const f of FEATURES) {
    const src = read(f)
    assert.match(src, /<HeaderActions \/>/, `${f} missing HeaderActions`)
    assert.ok(!src.includes('<ConfigDrawer'), `${f} still ships the layout-experiment drawer`)
    assert.ok(!/ms-auto flex items-center space-x-4'>\s*<ConnectionBanner/.test(src), `${f} still hand-rolls the action cluster`)
  }
  // The operator lifelines live in the shared component.
  const shared = read('components/header-actions.tsx')
  assert.match(shared, /<ReadyToggle \/>/)
  assert.match(shared, /<NotificationBell \/>/)
})

test('KB grew up: search, dialog manners, no native confirm, no emoji icon', () => {
  const kb = read('features/knowledge-base/index.tsx')
  assert.match(kb, /Search articles\.\.\./)
  assert.match(kb, /<ConfirmDialog/)
  assert.ok(!/\bconfirm\(/.test(kb), 'native confirm() survives')
  assert.ok(!kb.includes('📋'), 'emoji-as-icon survives')
  assert.match(kb, /role="dialog"/)
  assert.match(kb, /aria-modal="true"/)
  assert.match(kb, /e\.key === 'Escape'/)
  assert.match(kb, /toast\.error\("Couldn't save the article/)
  assert.ok(!/try \{[\s\S]{0,400}createKBEntry[\s\S]{0,200}\} finally \{ closeModal\(\) \}/.test(kb),
    'save still closes the modal on failure')
  assert.match(kb, /view_count > 0 &&/)  // "0 uses" noise gone
})

test('typing dots breathe (pulse), never bounce', () => {
  const detail = read('features/chats/detail.tsx')
  assert.ok(!detail.includes('animate-bounce'))
  const dots = detail.match(/animate-pulse/g) ?? []
  assert.ok(dots.length >= 3)
})

test('chat switch no longer refetches into a blank pane', () => {
  const detail = read('features/chats/detail.tsx')
  assert.ok(!/staleTime: 0,/.test(detail))
  assert.match(detail, /staleTime: 10_000/)
})

test('day separators split multi-day threads', () => {
  const detail = read('features/chats/detail.tsx')
  assert.match(detail, /showDay/)
  assert.match(detail, /toDateString\(\)/)
})

test('list rows have accessible names', () => {
  const chats = read('features/chats/index.tsx')
  assert.match(chats, /aria-label=\{`Conversation /)
})

test('template residue gone: dead links, alien copy, debug submit', () => {
  const signin = read('features/auth/sign-in/index.tsx')
  assert.ok(!signin.includes("href='/terms'"))
  assert.ok(!signin.includes("href='/privacy'"))
  const profile = read('features/settings/profile/index.tsx')
  assert.ok(!profile.includes('others will see you on the site'))
  const notif = read('features/settings/notifications/notifications-form.tsx')
  assert.ok(!notif.includes('showSubmittedData'))
  assert.ok(!notif.includes('friend requests'))
  assert.ok(!notif.includes('Marketing emails'))
  assert.match(notif, /No-agents alerts/)
})

test('avatar initials are muted, not neon', () => {
  const avatar = read('components/bbc-avatar.tsx')
  assert.ok(!avatar.includes('55%, 50%'))
  assert.match(avatar, /28%, 40%/)
})

test('lead-score mid tier no longer reads as disabled', () => {
  const detail = read('features/chats/detail.tsx')
  assert.ok(!/score >= 50 \? 'bg-muted-foreground'/.test(detail))
})
