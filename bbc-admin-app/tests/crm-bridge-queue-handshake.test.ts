/**
 * The handshake carries the queue, and chat:queue names the oldest one.
 *
 * The most important tests here are the two security ones (a hostile origin
 * or a non-parent window must never drive navigation) and the privacy one:
 * no message the bridge ever emits carries a `preview` key — the client's own
 * words stay inside the panel.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  __resetBridgeState,
  installCrmBridge,
  reportQueue,
  reportAttentionCycle,
  reportPresence,
} from '../src/lib/crm-bridge.ts'

const CRM = 'https://crm.buybusinessclass.com'
const EVIL = 'https://crm.attacker.example'

const allow = (origin: string) => {
  try {
    const u = new URL(origin)
    return u.protocol === 'https:' && u.hostname.endsWith('.buybusinessclass.com')
  } catch {
    return false
  }
}

type Posted = { msg: any; target: string }

function setup(opts: { embedded?: boolean; deps?: object } = {}) {
  const { embedded = true, deps = {} } = opts
  const posted: Posted[] = []
  const listeners: Array<(e: any) => void> = []
  const win: any = {
    location: { origin: 'https://chat.buybusinessclass.com' },
    addEventListener: (t: string, h: any) => {
      if (t === 'message') listeners.push(h)
    },
    removeEventListener: () => {},
  }
  win.parent = embedded
    ? { postMessage: (msg: any, target: string) => posted.push({ msg, target }) }
    : win
  ;(globalThis as any).window = win
  ;(globalThis as any).document = { referrer: `${CRM}/leads` }
  __resetBridgeState()
  installCrmBridge({ isAllowedOrigin: allow, ...deps })
  return {
    posted,
    fire: (data: any, origin: string, source: any = win.parent) =>
      listeners.forEach((h) => h({ data, origin, source })),
    restore: () => {
      delete (globalThis as any).window
      delete (globalThis as any).document
      __resetBridgeState()
    },
  }
}

// ── 1-2 · the handshake ───────────────────────────────────────────

test('crm:hello is answered with BOTH levels: unread and queued', () => {
  const env = setup({ deps: { getQueueCount: () => 3 } })
  try {
    env.fire({ source: 'bbc-crm', type: 'crm:hello' }, CRM)
    assert.equal(env.posted.length, 1)
    assert.deepEqual(env.posted[0].msg, {
      source: 'bbc-chat',
      type: 'chat:hello',
      unread: 0,
      queued: 3,
    })
  } finally {
    env.restore()
  }
})

test('the handshake primes the anti-noise guard', () => {
  const env = setup({ deps: { getQueueCount: () => 3 } })
  try {
    env.fire({ source: 'bbc-crm', type: 'crm:hello' }, CRM)
    env.posted.length = 0
    // The next heartbeat says the same number — nothing should go out.
    reportQueue(3)
    assert.deepEqual(env.posted, [], 'repeating what the handshake just said is noise')
  } finally {
    env.restore()
  }
})

// ── 3-5 · crm:focus-queue, and who may send it ────────────────────

test('crm:focus-queue calls onFocusQueue exactly once', () => {
  let calls = 0
  const env = setup({ deps: { onFocusQueue: () => calls++ } })
  try {
    env.fire({ source: 'bbc-crm', type: 'crm:focus-queue' }, CRM)
    assert.equal(calls, 1)
  } finally {
    env.restore()
  }
})

test('SECURITY: a disallowed origin cannot drive navigation', () => {
  let calls = 0
  const env = setup({ deps: { onFocusQueue: () => calls++ } })
  try {
    env.fire({ source: 'bbc-crm', type: 'crm:focus-queue' }, EVIL)
    assert.equal(calls, 0, 'a hostile page must not steer the agent panel')
  } finally {
    env.restore()
  }
})

test('SECURITY: a non-parent window is ignored even on an allowed origin', () => {
  let calls = 0
  const env = setup({ deps: { onFocusQueue: () => calls++ } })
  try {
    const sibling = { postMessage: () => {} }
    env.fire({ source: 'bbc-crm', type: 'crm:focus-queue' }, CRM, sibling)
    assert.equal(calls, 0)
  } finally {
    env.restore()
  }
})

// ── 6-7 · the optional fields ─────────────────────────────────────

test('optional fields travel when given and are ABSENT when undefined', () => {
  const env = setup()
  try {
    reportQueue(2, { conversationId: 'c-9', waitingSeconds: 41, route: 'HNL → AKL' })
    const withAll = env.posted.at(-1)!.msg
    assert.deepEqual(withAll, {
      source: 'bbc-chat',
      type: 'chat:queue',
      count: 2,
      conversationId: 'c-9',
      waitingSeconds: 41,
      route: 'HNL → AKL',
    })

    reportQueue(3, { conversationId: undefined, waitingSeconds: undefined, route: undefined })
    const bare = env.posted.at(-1)!.msg
    assert.deepEqual(
      Object.keys(bare).sort(),
      ['count', 'source', 'type'],
      'no explicit undefined keys in the payload'
    )
  } finally {
    env.restore()
  }
})

test('waitingSeconds never triggers a send on its own', () => {
  const env = setup()
  try {
    reportQueue(2, { waitingSeconds: 10 })
    reportQueue(2, { waitingSeconds: 15 })
    reportQueue(2, { waitingSeconds: 20 })
    assert.equal(
      env.posted.filter((p) => p.msg.type === 'chat:queue').length,
      1,
      'the age changes every 5s by definition — the guard stays on count alone'
    )
  } finally {
    env.restore()
  }
})

// ── 8 · privacy ───────────────────────────────────────────────────

test('no message the bridge emits ever carries a preview key', () => {
  const env = setup({ deps: { getQueueCount: () => 1 } })
  try {
    env.fire({ source: 'bbc-crm', type: 'crm:hello' }, CRM)
    reportQueue(4, { conversationId: 'c-1', waitingSeconds: 9, route: 'JFK → LHR' })
    reportAttentionCycle({
      attentionIds: ['c-1'],
      needsAgentIds: [],
      liveIds: ['c-1'],
      viewingId: null,
      newArrivalId: 'c-1',
      baselineTaken: true,
    })
    reportPresence(true)

    assert.ok(env.posted.length >= 3)
    for (const p of env.posted) {
      assert.ok(!('preview' in p.msg), `preview must never leave the panel: ${p.msg.type}`)
    }
  } finally {
    env.restore()
  }
})

// ── 9 · standalone ────────────────────────────────────────────────

test('standalone panel: zero messages, zero errors', () => {
  const env = setup({ embedded: false, deps: { getQueueCount: () => 5, onFocusQueue: () => {} } })
  try {
    reportQueue(5, { conversationId: 'c-1', waitingSeconds: 10 })
    assert.deepEqual(env.posted, [])
  } finally {
    env.restore()
  }
})

// ── 10 · regression: the old shapes are untouched ─────────────────

test('chat:queue without context is byte-identical to before', () => {
  const env = setup()
  try {
    reportQueue(2)
    assert.deepEqual(env.posted.at(-1)!.msg, { source: 'bbc-chat', type: 'chat:queue', count: 2 })
  } finally {
    env.restore()
  }
})
