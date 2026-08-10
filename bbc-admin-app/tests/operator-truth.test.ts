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

// ── P1: lead panel reachable below xl ──

test('lead panel: overlay toggle below xl, static default at xl', () => {
  assert.match(detail, /showLeadPanel/)
  assert.match(detail, /xl:static xl:block/)
  assert.match(detail, /xl:hidden[^"]*"[\s\S]{0,200}?Lead/)  // header chip hidden at xl
  assert.match(detail, /aria-expanded=\{showLeadPanel\}/)
})

// ── P1: on-navy text meets contrast; icon buttons named ──

test('navy header uses white/70, not muted-foreground', () => {
  const anchor = 'px-5 py-4 bg-[#0B1829]'  // the detail header, not the user bubble
  const start = detail.indexOf(anchor)
  const header = detail.slice(start, detail.indexOf('aria-label="Close conversation view"', start) + 60)
  assert.ok(!header.includes('text-muted-foreground'), 'muted-on-navy (≈3.9:1) survives in the header')
  assert.ok((header.match(/text-white\/70/g) ?? []).length >= 3)
  assert.match(header, /aria-label="Close conversation view"/)
  assert.match(header, /aria-label="Copy conversation id"/)
})

// ── P1: dark-mode variants on the hot status-color maps ──

test('status tints carry dark: variants', () => {
  assert.match(detail, /dark:bg-yellow-950/)   // TIER_COLORS gold
  assert.match(detail, /dark:bg-blue-950/)     // typing preview card
  assert.match(chats, /dark:bg-sky-950/)       // TAG_STYLES fresh
  assert.match(chats, /dark:bg-purple-950/)    // TUNNEL_STYLES support
  const kb = readFileSync(join(SRC, 'features/knowledge-base/index.tsx'), 'utf8')
  assert.match(kb, /dark:bg-blue-950/)
  const hot = readFileSync(join(SRC, 'features/dashboard/components/hot-leads.tsx'), 'utf8')
  assert.match(hot, /dark:bg-amber-950/)
})

// ── Owner decision: KPI side-tab borders removed ──

test('no side-tab border-l-4 left on KPI cards', () => {
  assert.ok(!kpi.includes('border-l-4'))
})
