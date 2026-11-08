// Operator truth — the critique's P0 + P1 fixes, pinned as source contracts.
// Runs with: node --experimental-strip-types --test tests/operator-truth.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const detail = readFileSync(join(SRC, 'features/chats/detail.tsx'), 'utf8')
const chats = readFileSync(join(SRC, 'features/chats/index.tsx'), 'utf8')
const kpi = readFileSync(join(SRC, 'features/dashboard/components/kpi-cards.tsx'), 'utf8')

// ── P0: silent failures are dead — every trust-critical catch speaks ──

test('send/claim/close failures surface a toast (no silent catch)', () => {
  assert.ok(!detail.includes('// send failed silently'))
  assert.ok(!detail.includes('// claim failed silently'))
  assert.ok(!detail.includes('// close failed silently'))
  const errorToasts = detail.match(/toast\.error\(/g) ?? []
  assert.ok(errorToasts.length >= 3, `expected ≥3 toast.error calls, got ${errorToasts.length}`)
  assert.match(detail, /toast\.success\('Conversation closed\.'\)/)
  assert.match(detail, /from 'sonner'/)
})

// ── P1: names survive closing ──

test('"Closed conversation" placeholder is gone from list and detail', () => {
  const marker = 'Closed' + ' conversation</span>'
  assert.ok(!detail.includes(marker), 'detail header still erases the name')
  assert.ok(!chats.includes(marker), 'list rows still erase the name')
})

// ── P1: the details stay reachable below full width ──

test('details: a column at full width, an overlay toggle below it', () => {
  assert.match(detail, /const detailsStatic = layout === 'wide'/)
  assert.match(detail, /showDetails/)
  assert.match(detail, /aria-expanded=\{showDetails\}/)
  assert.match(detail, /aria-label='Trip and lead'/)
})

// ── P1: icon buttons are named ──

test('the thread header names every icon button', () => {
  assert.match(detail, /aria-label='More actions'/)
  assert.match(detail, /aria-label=\{wide \? 'Back to three columns' : 'Wider chat'\}/)
  assert.match(detail, /aria-label='Back to the chats'/)
  assert.match(detail, /aria-label='Send'/)
})

// ── P1: dark mode can't fall through light-only tints ──

test('chat colors are palette tokens that flip in dark mode', () => {
  const theme = readFileSync(join(SRC, 'styles/theme.css'), 'utf8')
  const dark = theme.slice(theme.indexOf('.dark {'))
  for (const token of ['--k-fff3e0', '--k-efeaff', '--k-fdecea', '--k-e3f6ea', '--k-202020', '--k-ffffff']) {
    assert.match(dark, new RegExp(`${token}:`), `${token} has no dark value`)
  }
  // No light-only Tailwind tints left in the chat screens.
  for (const src of [detail, chats]) {
    assert.ok(!/\b(bg|text|border)-(amber|blue|green|red|sky|purple|emerald|slate|orange|yellow)-\d{2,3}\b/.test(src))
  }
  const kb = readFileSync(join(SRC, 'features/knowledge-base/index.tsx'), 'utf8')
  assert.match(kb, /dark:bg-blue-950/)
  const hot = readFileSync(join(SRC, 'features/dashboard/components/hot-leads.tsx'), 'utf8')
  assert.match(hot, /dark:bg-amber-950/)
})

// ── Owner decision: KPI side-tab borders removed ──

test('no side-tab border-l-4 left on KPI cards', () => {
  assert.ok(!kpi.includes('border-l-4'))
})
