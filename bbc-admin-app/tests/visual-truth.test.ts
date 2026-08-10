// Visual truth & mobile fit — the fixes from the impeccable audit.
// Runs with: node --experimental-strip-types --test tests/visual-truth.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { formatAge, formatDuration } from '../src/lib/format-age.ts'
import { resolveDataState } from '../src/lib/data-state.ts'
import { isDegenerateRoute } from '../src/lib/route-display.ts'
import { listRowDot } from '../src/features/chats/presence.ts'
import { lastSeenLabel, partitionLive } from '../src/features/dashboard/components/live-state.ts'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const NOW = new Date('2026-08-10T12:00:00Z')
const agoMin = (m: number) => new Date(NOW.getTime() - m * 60000).toISOString()

// ── formatAge / formatDuration: the m → h → d → mo caps table ──

test('formatDuration caps magnitudes sanely', () => {
  assert.equal(formatDuration(0), '0m')
  assert.equal(formatDuration(30), '30m')
  assert.equal(formatDuration(80), '1h 20m')
  assert.equal(formatDuration(120), '2h')
  assert.equal(formatDuration(33 * 60 + 12), '1d')       // "33h 12m" dies
  assert.equal(formatDuration(1447 * 60), '2mo')          // "1447h 30m" dies
  assert.equal(formatDuration(1518 * 60 + 50), '2mo')     // Hot Leads SLA badge
  assert.equal(formatDuration(3 * 24 * 60), '3d')
})

test('formatAge: just now, ago suffix, null on garbage', () => {
  assert.equal(formatAge(agoMin(0.5), NOW), 'just now')
  assert.equal(formatAge(agoMin(10), NOW), '10m ago')
  assert.equal(formatAge(agoMin(80), NOW), '1h 20m ago')
  assert.equal(formatAge(agoMin(60 * 24 * 61), NOW), '2mo ago')
  assert.equal(formatAge(null, NOW), null)
  assert.equal(formatAge('not-a-date', NOW), null)
})

test('lastSeenLabel uses the shared caps', () => {
  assert.equal(lastSeenLabel(agoMin(1447 * 60), NOW), 'last seen 2mo ago')
  assert.equal(lastSeenLabel(agoMin(12), NOW), 'last seen 12m ago')
})

// ── tri-state: error is never conflated with loading or zero ──

test('resolveDataState: error > loading > ready', () => {
  assert.equal(resolveDataState({ isLoading: true, isError: false }), 'loading')
  assert.equal(resolveDataState({ isLoading: true, isError: true }), 'error')
  assert.equal(resolveDataState({ isLoading: false, isError: true }), 'error')
  assert.equal(resolveDataState({ isLoading: false, isError: false }), 'ready')
})

test('error view never wears success copy (source contract)', () => {
  const view = readFileSync(join(SRC, 'components/data-state.tsx'), 'utf8')
  const errorBlock = view.slice(view.indexOf("state === 'error'"))
  assert.ok(!/All contacted|🎉|All leads/.test(errorBlock))
  assert.match(errorBlock, /Couldn&apos;t load/)
})

// ── list-row dots: green must be EARNED by recent activity ──

test('listRowDot: aged truth on list rows', () => {
  assert.equal(listRowDot('needs_agent', agoMin(1), NOW), 'bg-red-400')
  assert.equal(listRowDot('pending', agoMin(1), NOW), 'bg-yellow-400')
  assert.equal(listRowDot('closed', agoMin(1), NOW), 'bg-muted-foreground/50')
  assert.equal(listRowDot('active', agoMin(1), NOW), 'bg-emerald-500')     // fresh → green
  assert.notEqual(listRowDot('active', agoMin(3 * 24 * 60), NOW), 'bg-emerald-500') // 3d idle → NOT green
  assert.notEqual(listRowDot('active', undefined, NOW), 'bg-emerald-500')  // unknown → never green
})

// ── degenerate routes never presented as hot leads ──

test('isDegenerateRoute: LHR → LHR is noise, real routes pass', () => {
  assert.equal(isDegenerateRoute('LHR → LHR'), true)
  assert.equal(isDegenerateRoute('JFK → LHR'), false)
  assert.equal(isDegenerateRoute(null), false)
  assert.equal(isDegenerateRoute('LHR'), false)
})

// ── Team live: offline majority never dominates ──

test('partitionLive splits live from offline', () => {
  const a = (id: string, online: boolean) =>
    ({ id, name: id, role: 'sales', is_ready: false, is_online: online, last_seen: null }) as never
  const { live, offline } = partitionLive([a('x', true), a('y', false), a('z', false)])
  assert.deepEqual(live.map((v: { id: string }) => v.id), ['x'])
  assert.equal(offline.length, 2)
})

test('team live card collapses offline behind a toggle (source contract)', () => {
  const card = readFileSync(join(SRC, 'features/dashboard/components/team-live-card.tsx'), 'utf8')
  assert.match(card, /Show.*offline|offline.*Show/s)
  assert.match(card, /max-h-\d+ .*overflow-y-auto|overflow-y-auto/)
})

// ── chats mobile: master-detail, no fixed column inside a 375px viewport ──

test('chats index goes full-screen detail on mobile (source contract)', () => {
  const page = readFileSync(join(SRC, 'features/chats/index.tsx'), 'utf8')
  assert.match(page, /selectedId \? 'hidden md:flex' : 'flex'/)   // list hides under md when detail open
  assert.match(page, /hidden md:block w-1\.5/)                    // drag handle is desktop-only
  assert.ok(!page.includes('text-[10px]'), '10px text must not return')
  assert.match(page, /min-h-11 md:min-h-0/)                       // 44px touch targets on mobile chips
})

test('no 10px text anywhere in src (11px minimum)', () => {
  const walk = (dir: string): string[] => {
    const out: string[] = []
    for (const name of readdirSync(dir)) {
      const p = join(dir, name)
      if (statSync(p).isDirectory()) out.push(...walk(p))
      else if (/\.(tsx?|css)$/.test(name)) out.push(p)
    }
    return out
  }
  for (const file of walk(SRC)) {
    assert.ok(!readFileSync(file, 'utf8').includes('text-[10px]'), `10px text in ${file}`)
  }
})
