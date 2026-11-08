// Design renewal — one header everywhere, KB grown up, de-slopped details.
// Runs with: node --experimental-strip-types --test tests/design-renewal.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
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
  // The redesigned agent pages (Your day, Knowledge) carry it too.
  'features/day/index.tsx', 'features/knowledge/index.tsx',
]

test('ONE header: every page renders HeaderActions, none composes its own cluster', () => {
  for (const f of FEATURES) {
    const src = read(f)
    // `<HeaderActions />` or with props (`compact` on phones and in the CRM dock).
    assert.match(src, /<HeaderActions[\s/>]/, `${f} missing HeaderActions`)
    assert.ok(!src.includes('<ConfigDrawer'), `${f} still ships the layout-experiment drawer`)
    assert.ok(!/ms-auto flex items-center space-x-4'>\s*<ConnectionBanner/.test(src), `${f} still hand-rolls the action cluster`)
  }
  // The operator lifelines live in the shared component.
  const shared = read('components/header-actions.tsx')
  assert.match(shared, /<ReadyToggle\b/)
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
  // The template's Notifications, Account and Display pages are gone: their
  // switches saved values nothing read. Alerts replaced them with settings
  // the alert engine reads (lib/alert-prefs.ts, tests/alert-prefs.test.ts).
  for (const gone of ['notifications/notifications-form.tsx', 'account/account-form.tsx', 'display/display-form.tsx']) {
    assert.ok(!existsSync(join(SRC, 'features/settings', gone)), gone)
  }
  const alerts = read('features/settings/alerts/index.tsx')
  assert.ok(!alerts.includes('showSubmittedData'))
  assert.match(alerts, /writeAlertPrefs/)
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

// ── chat stream polish (from the owner's screenshot review) ──

test('chat stream: grouping, honest presence, one place for where they came from', () => {
  const detail = readFileSync(join(SRC, 'features/chats/detail.tsx'), 'utf8')
  assert.match(detail, /const grouped = !showDay/)          // same-speaker turns group
  // The redesign has no bubbles (text beside an avatar), so the old
  // min-width-bubble guard has nothing left to guard.
  assert.ok(!/opacity-50">\s*\{new Date\(msg\.created_at\)\.toLocaleTimeString/.test(detail),
    'bubble timestamps still at 50% opacity')
  // No presence on a closed thread — "on the site now" next to "Closed" was
  // the panel arguing with itself.
  assert.match(detail, /conv\.status !== 'closed' && presence\.label &&/)
  assert.ok(!/Closed \$\{new Date\(conv\.closed_at\)\.toLocaleDateString\(\)\}/.test(detail),
    'locale-roulette closed date survives')
  // Source/Source dedup: the brand is "Site", the campaign is "Source".
  const view = readFileSync(join(SRC, 'features/chats/view.ts'), 'utf8')
  assert.equal((view.match(/label: 'Site'/g) ?? []).length, 1)
  assert.equal((view.match(/label: 'Source'/g) ?? []).length, 1)
})
