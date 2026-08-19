/**
 * chat:queue — the CRM learns about the shared line. Badge only, never
 * auto-open: opening the panel on a queue signal would take over every
 * agent's screen every time anyone gets a chat.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  __resetBridgeState,
  installCrmBridge,
  reportQueue,
} from '../src/lib/crm-bridge.ts'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p: string) => readFileSync(join(root, p), 'utf-8')

const CRM = 'https://crm.buybusinessclass.com'

const allow = (origin: string) => {
  try {
    const u = new URL(origin)
    return u.protocol === 'https:' && u.hostname.endsWith('.buybusinessclass.com')
  } catch {
    return false
  }
}

type Posted = { msg: any; target: string }

function setup(embedded = true) {
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
  installCrmBridge({ isAllowedOrigin: allow })
  return {
    posted,
    restore: () => {
      delete (globalThis as any).window
      delete (globalThis as any).document
      __resetBridgeState()
    },
  }
}

test('chat:queue goes out on change, not on every cycle', () => {
  const env = setup()
  try {
    reportQueue(2)
    reportQueue(2)
    reportQueue(2)
    reportQueue(3)
    const q = env.posted.filter((p) => p.msg.type === 'chat:queue')
    assert.deepEqual(q.map((p) => p.msg.count), [2, 3])
  } finally {
    env.restore()
  }
})

test('count 0 is sent exactly once when the line empties', () => {
  const env = setup()
  try {
    reportQueue(2)
    reportQueue(0)
    reportQueue(0)
    reportQueue(0)
    const q = env.posted.filter((p) => p.msg.type === 'chat:queue')
    assert.deepEqual(q.map((p) => p.msg.count), [2, 0], 'the badge must switch off, once')
  } finally {
    env.restore()
  }
})

test('standalone panel sends nothing', () => {
  const env = setup(false)
  try {
    reportQueue(5)
    assert.deepEqual(env.posted, [])
  } finally {
    env.restore()
  }
})

test('chat:queue carries a count and nothing else — no ids, no text', () => {
  const env = setup()
  try {
    reportQueue(4)
    const q = env.posted.find((p) => p.msg.type === 'chat:queue')!
    assert.deepEqual(Object.keys(q.msg).sort(), ['count', 'source', 'type'])
    assert.equal(q.target, CRM)
  } finally {
    env.restore()
  }
})

test('panel badge and CRM count come from the same heartbeat value', () => {
  // One source: use-heartbeat feeds queue-store (the badge) and reportQueue
  // (the CRM) from the same response, in the same block.
  const src = read('src/hooks/use-heartbeat.ts')
  const start = src.indexOf('Array.isArray(res?.queue_ids)')
  assert.ok(start > 0, 'the queue block must exist in processResponse')
  const block = src.slice(start, start + 1200)
  assert.match(block, /setQueue\(ids, res\.queue_count \?\? ids\.length\)/)
  assert.match(block, /reportQueue\(res\.queue_count \?\? ids\.length\)/)
})
