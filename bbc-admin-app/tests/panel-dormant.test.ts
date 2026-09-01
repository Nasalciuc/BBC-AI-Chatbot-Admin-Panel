/**
 * A panel nobody is looking at stops asking.
 *
 * Operators live in the CRM with the chat panel minimised in the dock — and
 * an iframe hidden by CSS is NOT "hidden" to Page Visibility, so the browser
 * never slowed it down. Many keep a standalone panel tab open too: two full
 * pollers for one person.
 *
 * These tests pin the three things that must stay true:
 *  - dormant is a set of reasons, and any one of them is enough;
 *  - the CRM's visibility message only counts from the real parent, on an
 *    allow-listed origin (it reaches a store that silences polling — the same
 *    class of lever as the alert kill-switch in crm-bridge.test.ts);
 *  - NO interval anywhere is faster than it was on master.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

import { __resetBridgeState, installCrmBridge } from '../src/lib/crm-bridge.ts'
import { idleMinutes, IDLE_BADGE_MIN, IDLE_NUDGE_MIN } from '../src/features/chats/idle.ts'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p: string) => readFileSync(join(root, p), 'utf-8')

const CRM = 'https://crm.buybusinessclass.com'
const SELF = 'https://chat.buybusinessclass.com'
const EVIL = 'https://crm.attacker.example'

const allow = (origin: string) => {
  try {
    const u = new URL(origin)
    if (u.protocol !== 'https:' && u.hostname !== 'localhost') return false
    const h = u.hostname.toLowerCase()
    return h === 'buybusinessclass.com' || h.endsWith('.buybusinessclass.com') || h === 'localhost'
  } catch {
    return false
  }
}

function setup() {
  const listeners: Array<(e: any) => void> = []
  const win: any = {
    location: { origin: SELF },
    addEventListener: (t: string, h: any) => {
      if (t === 'message') listeners.push(h)
    },
    removeEventListener: (t: string, h: any) => {
      const i = listeners.indexOf(h)
      if (i >= 0) listeners.splice(i, 1)
    },
  }
  win.parent = { postMessage: () => {} }
  ;(globalThis as any).window = win
  ;(globalThis as any).document = { referrer: '' }
  __resetBridgeState()
  return {
    fire: (data: any, origin: string, source: any = win.parent) =>
      listeners.forEach((h) => h({ data, origin, source })),
    restore: () => {
      delete (globalThis as any).window
      delete (globalThis as any).document
      __resetBridgeState()
    },
  }
}

// ── crm:visibility is a lever; it must be as guarded as the rest ──

test('crm:visibility from the real parent on an allowed origin is delivered', () => {
  const env = setup()
  try {
    const seen: boolean[] = []
    const cleanup = installCrmBridge({ isAllowedOrigin: allow, onVisibility: (h) => seen.push(h) })
    env.fire({ source: 'bbc-crm', type: 'crm:visibility', hidden: true }, CRM)
    env.fire({ source: 'bbc-crm', type: 'crm:visibility', hidden: false }, CRM)
    cleanup()
    assert.deepEqual(seen, [true, false])
  } finally {
    env.restore()
  }
})

test('crm:visibility from a NOT allow-listed origin is ignored', () => {
  const env = setup()
  try {
    const seen: boolean[] = []
    const cleanup = installCrmBridge({ isAllowedOrigin: allow, onVisibility: (h) => seen.push(h) })
    env.fire({ source: 'bbc-crm', type: 'crm:visibility', hidden: true }, EVIL)
    cleanup()
    assert.deepEqual(seen, [], 'a stranger must not be able to silence our polling')
  } finally {
    env.restore()
  }
})

test('crm:visibility from a sibling frame (source !== parent) is ignored', () => {
  const env = setup()
  try {
    const seen: boolean[] = []
    const cleanup = installCrmBridge({ isAllowedOrigin: allow, onVisibility: (h) => seen.push(h) })
    env.fire({ source: 'bbc-crm', type: 'crm:visibility', hidden: true }, CRM, { notTheParent: true })
    cleanup()
    assert.deepEqual(seen, [])
  } finally {
    env.restore()
  }
})

test('a message without the bbc-crm source is ignored', () => {
  const env = setup()
  try {
    const seen: boolean[] = []
    const cleanup = installCrmBridge({ isAllowedOrigin: allow, onVisibility: (h) => seen.push(h) })
    env.fire({ type: 'crm:visibility', hidden: true }, CRM)
    cleanup()
    assert.deepEqual(seen, [])
  } finally {
    env.restore()
  }
})

test('hidden is coerced, never trusted as-is', () => {
  const env = setup()
  try {
    const seen: boolean[] = []
    const cleanup = installCrmBridge({ isAllowedOrigin: allow, onVisibility: (h) => seen.push(h) })
    env.fire({ source: 'bbc-crm', type: 'crm:visibility', hidden: 'yes' }, CRM)
    env.fire({ source: 'bbc-crm', type: 'crm:visibility' }, CRM)
    cleanup()
    assert.deepEqual(seen, [true, false])
  } finally {
    env.restore()
  }
})

// ── Idle is about a desk, not a quiet client ─────────────────────

const HUMAN = { mode: 'human', assigned_agent_id: 'a1' }
const NOW = new Date('2026-09-01T12:00:00Z').getTime()
const agoISO = (min: number) => new Date(NOW - min * 60_000).toISOString()

test('an AI-handled chat is never idle — that is the class #200 exists to save', () => {
  assert.equal(
    idleMinutes({ mode: 'ai', assigned_agent_id: null, last_user_message_at: agoISO(120) }, NOW),
    null,
  )
})

test('human mode without an assigned agent does not count either', () => {
  assert.equal(
    idleMinutes({ mode: 'human', assigned_agent_id: null, last_user_message_at: agoISO(120) }, NOW),
    null,
  )
})

test('no clocks at all → the question does not apply', () => {
  assert.equal(idleMinutes({ ...HUMAN }, NOW), null)
  assert.equal(idleMinutes({ ...HUMAN, last_user_message_at: null }, NOW), null)
})

test('an unparseable timestamp is not an idle chat', () => {
  assert.equal(idleMinutes({ ...HUMAN, last_agent_message_at: 'not-a-date' }, NOW), null)
})

test('idle counts from the LATEST side, whoever spoke last', () => {
  assert.equal(
    idleMinutes({ ...HUMAN, last_user_message_at: agoISO(90), last_agent_message_at: agoISO(20) }, NOW),
    20,
  )
})

test('the badge threshold is 15 and the nudge threshold is 30', () => {
  assert.equal(IDLE_BADGE_MIN, 15)
  assert.equal(IDLE_NUDGE_MIN, 30)
  assert.ok(idleMinutes({ ...HUMAN, last_user_message_at: agoISO(14) }, NOW)! < IDLE_BADGE_MIN)
  assert.ok(idleMinutes({ ...HUMAN, last_user_message_at: agoISO(15) }, NOW)! >= IDLE_BADGE_MIN)
})

// ── The store: reasons are independent, and the no-op is a real no-op ──

test('dormant is a set of reasons — any one is enough, all must clear', async () => {
  const { usePanelModeStore } = await import('../src/stores/panel-mode-store.ts')
  const { setReason } = usePanelModeStore.getState()

  setReason('tab_hidden', true)
  assert.equal(usePanelModeStore.getState().dormant, true)
  setReason('crm_hidden', true)
  assert.equal(usePanelModeStore.getState().dormant, true)
  setReason('tab_hidden', false)
  assert.equal(usePanelModeStore.getState().dormant, true, 'crm_hidden still holds it')
  setReason('crm_hidden', false)
  assert.equal(usePanelModeStore.getState().dormant, false)
})

test('setting a reason to the value it already has changes nothing', async () => {
  const { usePanelModeStore } = await import('../src/stores/panel-mode-store.ts')
  const { setReason } = usePanelModeStore.getState()
  setReason('not_leader', true)
  const before = usePanelModeStore.getState().reasons
  setReason('not_leader', true)
  assert.equal(usePanelModeStore.getState().reasons, before, 'same Set → no re-render')
  setReason('not_leader', false)
})

// ── The worker learns two messages and forgets nothing ───────────

test('the worker reprograms its interval instead of being rebuilt', () => {
  const w = read('public/heartbeat-worker.js')
  assert.match(w, /msg\.type === 'setInterval'/)
  assert.match(w, /clearInterval\(_interval\)[\s\S]*setInterval\(doHeartbeat, ms\)/)
  assert.match(w, /msg\.type === 'pingNow'/)
  // The worker never tears itself down: rebuilding it would lose the token
  // and re-fire the immediate first ping on every mode change.
  assert.doesNotMatch(w, /self\.close\(\)/)
})

test('leaving dormant pings at once; dormant slows down, never speeds up', () => {
  const h = read('src/hooks/use-heartbeat.ts')
  assert.match(h, /dormant \? \(queueCount > 0 \? 5_000 : 15_000\) : intervalMs/)
  assert.match(h, /type: 'setInterval', ms/)
  assert.match(h, /wasDormant\.current && !dormant/)
  assert.match(h, /type: 'pingNow'/)
  // The cadence effect must not be able to tear the worker down.
  assert.match(h, /\}, \[dormant, queueCount, intervalMs\]\)/)
})

// ── Nothing anywhere got faster ──────────────────────────────────

/** Every numeric refetchInterval in src/, as written on this branch. */
const INTERVALS_ON_MASTER: Record<string, number[]> = {
  'src/features/chats/detail.tsx': [500, 2_000, 2_000],
  'src/features/chats/index.tsx': [30_000, 5_000, 10_000],
  'src/features/chats/components/queue-section.tsx': [5_000],
  'src/features/dashboard/index.tsx': [60_000],
  'src/features/dashboard/components/team-live-card.tsx': [30_000],
  'src/components/notification-bell.tsx': [30_000],
}

test('no refetchInterval got shorter than it was on master', () => {
  for (const [file, expected] of Object.entries(INTERVALS_ON_MASTER)) {
    const src = read(file)
    const found = [...src.matchAll(/refetchInterval:[^\n]*?(\d[\d_]*)/g)].map((m) =>
      Number(m[1].replaceAll('_', '')),
    )
    assert.deepEqual(
      found,
      expected,
      `${file}: intervals must stay exactly as they were — dormant turns polling OFF, it never speeds it up`,
    )
  }
})

test('every polling site honours dormant', () => {
  for (const file of Object.keys(INTERVALS_ON_MASTER)) {
    const src = read(file)
    for (const line of src.split('\n')) {
      if (!line.includes('refetchInterval:')) continue
      assert.match(line, /dormant \?/, `${file}: "${line.trim()}" ignores dormant`)
    }
    assert.match(src, /usePanelModeStore/, `${file} does not read the panel mode`)
  }
})

test('the connection banner stops checking when nobody is looking', () => {
  const src = read('src/components/connection-banner.tsx')
  assert.match(src, /if \(dormant\) return/)
  assert.match(src, /\}, \[dormant\]\)/)
  assert.match(src, /setInterval\(check, 30000\)/, 'the interval itself is unchanged')
})

// ── One consumer per human ───────────────────────────────────────

test('the embedded panel leads, a standalone panel dozes, and it recovers alone', () => {
  const src = read('src/lib/panel-leader.ts')
  assert.match(src, /isEmbedded\(\)/)
  assert.match(src, /const LEADER_TIMEOUT_MS = 15_000/)
  assert.match(src, /setReason|set\('not_leader', true\)/)
  // The watchdog is what makes a closed CRM tab recoverable without a handshake.
  assert.match(src, /setTimeout\(\(\) => set\('not_leader', false\), LEADER_TIMEOUT_MS\)/)
  // Cleanup must release the claim, or a remount leaves the panel silent.
  assert.match(src, /set\('not_leader', false\)\r?\n\s*ch\.close\(\)/)
  assert.match(src, /typeof BroadcastChannel === 'undefined'/, 'must degrade, not throw')
})

test('the layout wires all three dormant reasons and shows the leader banner', () => {
  const src = read('src/components/layout/authenticated-layout.tsx')
  assert.match(src, /onVisibility: \(hidden\) =>/)
  assert.match(src, /setReason\('crm_hidden', hidden\)/)
  assert.match(src, /visibilitychange/)
  assert.match(src, /set\('tab_hidden', document\.hidden\)/)
  assert.match(src, /installPanelLeader\(\)/)
  assert.match(src, /it's active in the CRM/)
})

// ── The list shows what is alive, and offers no close-all ────────

test('the list badges, sorts and nudges — and never offers to close them all', () => {
  const src = read('src/features/chats/index.tsx')
  assert.match(src, /Idle \{idle\}m/)
  assert.match(src, /No message from either side/)
  assert.match(src, /const ordered = useMemo/)
  assert.match(src, /ordered\.map\(conv =>/)
  assert.match(src, /chats with no activity for over/)
  assert.match(src, /Not now/)
  assert.match(src, /!dormant && idleCount >= 3/)
  assert.doesNotMatch(src, /close all|Close all|closeAll/i)
})

// ── CodeRabbit #219: the dormant panel really stops asking ───────

test('refreshAttention asks for nothing while dormant — before fetchQuery', () => {
  const h = read('src/hooks/use-heartbeat.ts')
  const start = h.indexOf('const refreshAttention')
  const body = h.slice(start, h.indexOf('setInterval(refreshAttention'))
  const dormantGuard = body.indexOf('usePanelModeStore.getState().dormant')
  const fetch = body.indexOf('queryClient.fetchQuery')
  assert.ok(dormantGuard >= 0, 'refreshAttention must read dormancy at call time')
  assert.ok(fetch >= 0, 'refreshAttention still fetches when awake')
  assert.ok(dormantGuard < fetch, 'the dormant return must precede fetchQuery')
  assert.match(body, /if \(usePanelModeStore\.getState\(\)\.dormant\) return/)
  assert.match(body, /queryKey: ATTENTION_QUERY_KEY/)
})

test('waking from dormant invalidates the attention query once', () => {
  const h = read('src/hooks/use-heartbeat.ts')
  const start = h.indexOf('wasDormant.current && !dormant')
  const body = h.slice(start, h.indexOf('wasDormant.current = dormant'))
  assert.match(body, /type: 'pingNow'/)
  assert.match(body, /invalidateQueries\(\{ queryKey: ATTENTION_QUERY_KEY \}\)/)
  assert.equal(
    [...body.matchAll(/invalidateQueries/g)].length,
    1,
    'wake must invalidate exactly once, not on every cadence tick',
  )
})

test('worker-less fallback ping enforces 15s empty / 5s queued while dormant', () => {
  const h = read('src/hooks/use-heartbeat.ts')
  assert.match(h, /const lastFallbackPing = useRef\(0\)/)
  const ping = h.slice(h.indexOf('const ping ='), h.indexOf('const refreshAttention'))
  assert.match(ping, /usePanelModeStore\.getState\(\)\.dormant/)
  assert.match(ping, /queueCount > 0 \? 5_000 : 15_000/)
  assert.match(ping, /nowMs - lastFallbackPing\.current < gap - 250/)
  // The interval the fallback still owns is intervalMs (5s). The guard must
  // skip those ticks when dormant and the queue is empty.
  const skip = (elapsed: number, queueCount: number) => {
    const gap = queueCount > 0 ? 5_000 : 15_000
    return elapsed < gap - 250
  }
  assert.equal(skip(5_000, 0), true, 'empty queue: a 5s tick is skipped')
  assert.equal(skip(15_000, 0), false, 'empty queue: a 15s tick is allowed')
  assert.equal(skip(5_000, 2), false, 'queue of 2 keeps the 5s beat')
})

test('Idle toggle is outside canFilterTag so sales can turn the filter off', () => {
  const src = read('src/features/chats/index.tsx')
  assert.match(src, /canFilterTag = \['owner', 'admin', 'dev', 'supervisor', 'qa'\]\.includes\(role\)/)
  assert.doesNotMatch(src, /canFilterTag = \[[^\]]*sales/)
  const gate = src.indexOf('{canFilterTag && (')
  const idleView = src.indexOf('Idle is a view, not a permission')
  const idleToggle = src.indexOf('setIdleOnly((v) => !v)')
  assert.ok(gate >= 0 && idleView >= 0 && idleToggle >= 0)
  assert.ok(idleView > gate, 'Idle chip is declared after the tag gate')
  assert.ok(idleToggle > idleView, 'the toggle lives in the ungated wrap')
  // The trap: Review enables idleOnly for every role, including sales.
  assert.match(src, /setIdleOnly\(true\); dismissNudge\(\)/)
})

const NUDGE_KEY = 'bbc_idle_nudge_seen'

function installSessionStorage(store: Record<string, string> | { throwOnGet?: boolean; throwOnSet?: boolean }) {
  const throwing = 'throwOnGet' in store || 'throwOnSet' in store
  const data: Record<string, string> = throwing ? {} : (store as Record<string, string>)
  const flags = store as { throwOnGet?: boolean; throwOnSet?: boolean }
  const prev = (globalThis as any).sessionStorage
  ;(globalThis as any).sessionStorage = {
    getItem: (k: string) => {
      if (flags.throwOnGet) throw new Error('private mode')
      return data[k] ?? null
    },
    setItem: (k: string, v: string) => {
      if (flags.throwOnSet) throw new Error('private mode')
      data[k] = v
    },
  }
  return {
    data,
    restore: () => {
      if (prev === undefined) delete (globalThis as any).sessionStorage
      else (globalThis as any).sessionStorage = prev
    },
  }
}

/** Mirrors the component: session flag + in-memory dismiss. Remount = new instance. */
function nudgeSession() {
  let dismissed: boolean
  try {
    dismissed = sessionStorage.getItem(NUDGE_KEY) === '1'
  } catch {
    dismissed = false
  }
  const dismiss = () => {
    dismissed = true
    try {
      sessionStorage.setItem(NUDGE_KEY, '1')
    } catch {
      /* private mode: the in-memory flag still holds for this mount */
    }
  }
  const show = (idleCount: number, dormant = false) => !dormant && idleCount >= 3 && !dismissed
  return { show, dismiss, get dismissed() { return dismissed } }
}

test('nudge is once per session: Review writes the flag and a remount stays quiet', () => {
  const src = read('src/features/chats/index.tsx')
  assert.match(src, /sessionStorage\.getItem\('bbc_idle_nudge_seen'\) === '1'/)
  assert.match(src, /sessionStorage\.setItem\('bbc_idle_nudge_seen', '1'/)
  assert.match(src, /idleCount >= 3 && !nudgeDismissed/)
  assert.match(src, /setIdleOnly\(true\); dismissNudge\(\)/)
  assert.doesNotMatch(src, /nudgeDismissedAt/)

  const ss = installSessionStorage({})
  try {
    const first = nudgeSession()
    assert.equal(first.show(4), true, 'four idle chats show the banner')
    first.dismiss() // Review
    assert.equal(first.show(4), false)
    assert.equal(ss.data[NUDGE_KEY], '1')
    const remount = nudgeSession()
    assert.equal(remount.show(5), false, 'five idle chats after remount must not bring it back')
  } finally {
    ss.restore()
  }
})

test('Not now has the same effect on the banner as Review', () => {
  const src = read('src/features/chats/index.tsx')
  assert.match(src, /onClick=\{dismissNudge\}/)
  const ss = installSessionStorage({})
  try {
    const s = nudgeSession()
    assert.equal(s.show(4), true)
    s.dismiss() // Not now
    assert.equal(s.show(4), false)
    assert.equal(ss.data[NUDGE_KEY], '1')
    assert.equal(nudgeSession().show(4), false)
  } finally {
    ss.restore()
  }
})

test('sessionStorage throwing does not crash; the banner hides for this mount', () => {
  const src = read('src/features/chats/index.tsx')
  assert.match(src, /private mode: the in-memory flag still holds for this mount/)
  const ss = installSessionStorage({ throwOnGet: true, throwOnSet: true })
  try {
    const s = nudgeSession()
    assert.equal(s.show(4), true, 'unreadable storage starts unseen')
    assert.doesNotThrow(() => s.dismiss())
    assert.equal(s.show(4), false, 'in-memory flag still hides it')
  } finally {
    ss.restore()
  }
})

test('shared contracts live in src/lib/types.ts', () => {
  const types = read('src/lib/types.ts')
  assert.match(types, /export type PanelDormancyReason = 'tab_hidden' \| 'crm_hidden' \| 'not_leader'/)
  assert.match(types, /export interface CrmBridgeDeps/)
  const bridge = read('src/lib/crm-bridge.ts')
  assert.match(bridge, /import type \{ CrmBridgeDeps \} from '@\/lib\/types'/)
  assert.match(bridge, /export type \{ CrmBridgeDeps \}/)
  assert.match(bridge, /export type ChatBridgeMessage/)
  assert.doesNotMatch(bridge, /export type CrmBridgeDeps =/)
  const store = read('src/stores/panel-mode-store.ts')
  assert.match(store, /import type \{ PanelDormancyReason \} from '@\/lib\/types'/)
  assert.doesNotMatch(store, /type Reason =/)
})

