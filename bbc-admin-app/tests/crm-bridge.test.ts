/**
 * The panel → CRM bridge.
 *
 * The CRM builds an auto-open, a desktop notification and a badge on these
 * messages, so the contract has to be exact: never a wildcard target origin,
 * never a field carrying a client's words, never a second "come to the front"
 * for the same arrival, and a count that is a QUEUE DEPTH rather than a
 * per-tick delta.
 *
 * The bridge is a dependency-free module precisely so these can be real
 * behavioural tests instead of source-contract greps. The wiring inside
 * use-heartbeat.ts cannot be imported (it pulls `@/` aliases and Vite's
 * `import.meta.env`), so that half is pinned by source contract at the end.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  __resetBridgeState,
  installCrmBridge,
  isEmbedded,
  reportAttentionCycle,
  reportPresence,
  sendToCrm,
} from '../src/lib/crm-bridge.ts'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p: string) => readFileSync(join(root, p), 'utf-8')

const CRM = 'https://crm.buybusinessclass.com'
const SELF = 'https://chat.buybusinessclass.com'
const EVIL = 'https://crm.attacker.example'

/** A stand-in for isAllowedCrmOrigin. Deliberately as permissive as the real
 *  one — including our OWN origin, which is a buybusinessclass.com subdomain
 *  like any other. A stricter double would hide the bugs it must expose. */
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

type Posted = { msg: any; target: string }

function setup(opts: { embedded?: boolean; referrer?: string; throwOnPost?: boolean } = {}) {
  const { embedded = true, referrer = '', throwOnPost = false } = opts
  const posted: Posted[] = []
  const listeners: Array<(e: any) => void> = []
  const warnings: unknown[][] = []

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
  win.parent = embedded
    ? {
        postMessage: (msg: any, target: string) => {
          if (throwOnPost) throw new Error('parent went away')
          posted.push({ msg, target })
        },
      }
    : win

  ;(globalThis as any).window = win
  ;(globalThis as any).document = { referrer }
  const realWarn = console.warn
  console.warn = (...args: unknown[]) => {
    warnings.push(args)
  }

  __resetBridgeState()

  return {
    posted,
    warnings,
    parent: win.parent,
    /** Defaults to the real parent; pass a different source to model a
     *  sibling frame or an opener. */
    fire: (data: any, origin: string, source: any = win.parent) =>
      listeners.forEach((h) => h({ data, origin, source })),
    listenerCount: () => listeners.length,
    restore: () => {
      console.warn = realWarn
      delete (globalThis as any).window
      delete (globalThis as any).document
      __resetBridgeState()
    },
  }
}

const deps = { isAllowedOrigin: allow }

type CycleOpts = Parameters<typeof reportAttentionCycle>[0]
const cycle = (o: Partial<CycleOpts> = {}) =>
  reportAttentionCycle({
    attentionIds: [],
    needsAgentIds: [],
    liveIds: [],
    viewingId: null,
    newArrivalId: null,
    baselineTaken: true,
    ...o,
  })

/** Install and complete the handshake, then forget the traffic it produced. */
function connected(env: ReturnType<typeof setup>) {
  installCrmBridge(deps)
  env.fire({ source: 'bbc-crm', type: 'crm:hello' }, CRM)
  env.posted.length = 0
}

// ── The standalone panel is untouched ─────────────────────────────

test('not embedded: nothing is ever posted, and no listener is installed', () => {
  const env = setup({ embedded: false, referrer: CRM })
  try {
    assert.equal(isEmbedded(), false)
    const cleanup = installCrmBridge(deps)
    cycle({ attentionIds: ['c1'], liveIds: ['c1'], newArrivalId: 'c1' })
    reportPresence(true)
    sendToCrm({ type: 'chat:unread', unread: 9 })
    cleanup()
    assert.deepEqual(env.posted, [])
    assert.equal(env.listenerCount(), 0, 'a panel in its own tab installs nothing')
    assert.deepEqual(env.warnings, [], 'and says nothing in the console either')
  } finally {
    env.restore()
  }
})

// ── unread is a LEVEL, not a per-tick delta ───────────────────────

test('a waiting conversation stays counted after the heartbeat stops mentioning it', () => {
  const env = setup()
  try {
    connected(env)
    // Cycle 1: c1 arrives and the panel rings about it.
    cycle({ attentionIds: ['c1'], liveIds: ['c1'], newArrivalId: 'c1' })
    // Cycles 2 and 3: nobody answered. The heartbeat's set is rebuilt per
    // cycle and requires a delta, so c1 is no longer in it — but the client
    // is still waiting, so the badge must not drop.
    cycle({ liveIds: ['c1'] })
    cycle({ liveIds: ['c1'] })

    assert.deepEqual(
      env.posted.map((p) => [p.msg.type, p.msg.unread]),
      [['chat:incoming', 1]],
      'one arrival message, and no "back to zero" lie behind it'
    )
  } finally {
    env.restore()
  }
})

test('two clients waiting are reported as two, not one', () => {
  const env = setup()
  try {
    connected(env)
    cycle({ attentionIds: ['c1'], liveIds: ['c1'], newArrivalId: 'c1' })
    cycle({ attentionIds: ['c2'], liveIds: ['c1', 'c2'], newArrivalId: 'c2' })

    assert.deepEqual(
      env.posted.map((p) => [p.msg.type, p.msg.unread]),
      [
        ['chat:incoming', 1],
        ['chat:incoming', 2],
      ],
      'the second arrival must not overwrite the first — that is a queue depth'
    )
  } finally {
    env.restore()
  }
})

test('opening a conversation drains it from the count', () => {
  const env = setup()
  try {
    connected(env)
    cycle({ attentionIds: ['c1', 'c2'], liveIds: ['c1', 'c2'], newArrivalId: 'c1' })
    assert.equal(env.posted.at(-1)!.msg.unread, 2)

    cycle({ liveIds: ['c1', 'c2'], viewingId: 'c1' })
    assert.equal(env.posted.at(-1)!.msg.unread, 1)

    cycle({ liveIds: ['c1', 'c2'], viewingId: 'c2' })
    assert.equal(env.posted.at(-1)!.msg.unread, 0)
  } finally {
    env.restore()
  }
})

test('a conversation that leaves the list stops being counted', () => {
  const env = setup()
  try {
    connected(env)
    cycle({ attentionIds: ['c1'], liveIds: ['c1'], newArrivalId: 'c1' })
    assert.equal(env.posted.at(-1)!.msg.unread, 1)

    // Closed, or re-assigned to somebody else.
    cycle({ liveIds: [] })
    assert.deepEqual(env.posted.at(-1)!.msg, { source: 'bbc-chat', type: 'chat:unread', unread: 0 })
  } finally {
    env.restore()
  }
})

test('the server-owned needs_agent set survives the baseline pass', () => {
  const env = setup()
  try {
    connected(env)
    // The heartbeat's baseline cycle deliberately reports nothing: everything
    // on screen at mount goes into `attended` so logging in never rings. Two
    // clients are nevertheless waiting for a first reply, and the backend
    // says so with status needs_agent.
    cycle({
      attentionIds: [],
      needsAgentIds: ['c1', 'c2'],
      liveIds: ['c1', 'c2'],
      newArrivalId: null,
      baselineTaken: false,
    })

    assert.deepEqual(env.posted.map((p) => [p.msg.type, p.msg.unread]), [['chat:unread', 2]])
  } finally {
    env.restore()
  }
})

test('the baseline cycle updates the badge but never takes the screen', () => {
  const env = setup()
  try {
    connected(env)
    cycle({ attentionIds: ['c1'], liveIds: ['c1'], newArrivalId: 'c1', baselineTaken: false })

    assert.equal(env.posted.length, 1)
    assert.equal(
      env.posted[0].msg.type,
      'chat:unread',
      'otherwise the CRM jumps in front of the agent on every single reload'
    )
    assert.equal(env.posted[0].msg.unread, 1)
  } finally {
    env.restore()
  }
})

test('the same level twice sends one message, not two', () => {
  const env = setup()
  try {
    connected(env)
    cycle({ attentionIds: ['c1'], liveIds: ['c1'] })
    cycle({ liveIds: ['c1'] })
    cycle({ liveIds: ['c1'] })

    assert.equal(env.posted.length, 1, 'at one cycle per 5s this saves ~720 messages an hour')
    assert.equal(env.posted[0].msg.type, 'chat:unread')
  } finally {
    env.restore()
  }
})

test('an arrival gets one chat:incoming; later traffic in it never gets a second', () => {
  const env = setup()
  try {
    connected(env)
    cycle({ attentionIds: ['c1'], liveIds: ['c1'], newArrivalId: 'c1' })
    cycle({ liveIds: ['c1'] })
    // The client sends a SECOND message in the same conversation: the
    // heartbeat surfaces it again through gotNewMessage, but it is not a new
    // arrival and must not take the screen again.
    cycle({ attentionIds: ['c1'], liveIds: ['c1'] })

    assert.equal(
      env.posted.filter((p) => p.msg.type === 'chat:incoming').length,
      1,
      'the CRM may take the screen once per arrival, never twice'
    )
  } finally {
    env.restore()
  }
})

test('two arrivals in one cycle produce ONE chat:incoming carrying the total', () => {
  const env = setup()
  try {
    connected(env)
    cycle({ attentionIds: ['c1', 'c2'], liveIds: ['c1', 'c2'], newArrivalId: 'c1' })

    assert.equal(env.posted.length, 1)
    assert.deepEqual(env.posted[0].msg, {
      source: 'bbc-chat',
      type: 'chat:incoming',
      unread: 2,
      conversationId: 'c1',
    })
  } finally {
    env.restore()
  }
})

// ── The handshake ─────────────────────────────────────────────────

test('crm:hello is answered on that origin, with the real level', () => {
  const env = setup()
  try {
    installCrmBridge(deps)
    env.fire({ source: 'bbc-crm', type: 'crm:hello' }, CRM)
    env.posted.length = 0
    cycle({ needsAgentIds: ['c1', 'c2'], liveIds: ['c1', 'c2'] })
    env.posted.length = 0

    // A mid-shift reload: the CRM says hello again and must not be told zero.
    // The handshake now carries BOTH levels (queue-handshake wave); without a
    // getQueueCount injected, queued reads 0.
    env.fire({ source: 'bbc-crm', type: 'crm:hello' }, CRM)
    assert.deepEqual(env.posted[0].msg, {
      source: 'bbc-chat',
      type: 'chat:hello',
      unread: 2,
      queued: 0,
    })
    assert.equal(env.posted[0].target, CRM)
  } finally {
    env.restore()
  }
})

test('crm:hello from a disallowed origin is ignored AND the origin is not remembered', () => {
  const env = setup()
  try {
    installCrmBridge(deps)
    env.fire({ source: 'bbc-crm', type: 'crm:hello' }, EVIL)
    assert.deepEqual(env.posted, [], 'no reply')
    cycle({ attentionIds: ['c1'], liveIds: ['c1'], newArrivalId: 'c1' })
    assert.deepEqual(env.posted, [], 'and nothing is posted to it afterwards either')
  } finally {
    env.restore()
  }
})

test('a sibling frame on an allowed origin cannot hijack the target and mute us', () => {
  const env = setup({ referrer: `${CRM}/leads/1` })
  try {
    installCrmBridge(deps)
    // A window that is NOT our parent — another frame on the CRM page, an
    // opener, a nested frame — says hello. If it were believed, every later
    // post to the real parent would be dropped for origin mismatch: a silent,
    // permanent kill switch on the agent's alerts.
    const sibling = { postMessage: () => {} }
    env.fire({ source: 'bbc-crm', type: 'crm:hello' }, 'https://widget.buybusinessclass.com', sibling)
    assert.deepEqual(env.posted, [], 'no reply to a non-parent')

    cycle({ attentionIds: ['c1'], liveIds: ['c1'], newArrivalId: 'c1' })
    assert.equal(env.posted.length, 1, 'and the bridge still works afterwards')
    assert.equal(env.posted[0].target, CRM, 'still pointed at the real parent')
  } finally {
    env.restore()
  }
})

test('a message without source bbc-crm is ignored completely', () => {
  const env = setup()
  try {
    installCrmBridge(deps)
    env.fire({ type: 'crm:hello' }, CRM)
    env.fire({ source: 'some-extension', type: 'crm:hello' }, CRM)
    env.fire(null, CRM)
    env.fire('a string', CRM)
    assert.deepEqual(env.posted, [])
  } finally {
    env.restore()
  }
})

// ── Security and privacy ──────────────────────────────────────────

test('targetOrigin is never the wildcard', () => {
  const env = setup({ referrer: `${CRM}/agents/42` })
  try {
    connected(env)
    cycle({ attentionIds: ['c9'], liveIds: ['c9'], newArrivalId: 'c9' })
    reportPresence(true)

    assert.ok(env.posted.length >= 2)
    for (const p of env.posted) {
      assert.notEqual(p.target, '*', 'a wildcard lets any framing site read the agent state')
      assert.equal(p.target, CRM)
    }
  } finally {
    env.restore()
  }
})

test('an allowed referrer works before the handshake, but our OWN origin never does', () => {
  const withCrm = setup({ referrer: `${CRM}/leads/8842` })
  try {
    installCrmBridge(deps)
    cycle({ attentionIds: ['c1'], liveIds: ['c1'], newArrivalId: 'c1' })
    assert.equal(withCrm.posted.length, 1)
    assert.equal(withCrm.posted[0].target, CRM, 'origin, not the full referrer URL')
  } finally {
    withCrm.restore()
  }

  // After an in-frame navigation the referrer is the panel itself. That
  // origin IS on the allow-list — chat.buybusinessclass.com is a subdomain
  // like any other — so without an explicit check we would post to a parent
  // that is not us, and the browser would drop every message in silence.
  const withSelf = setup({ referrer: `${SELF}/chats` })
  try {
    installCrmBridge(deps)
    cycle({ attentionIds: ['c1'], liveIds: ['c1'], newArrivalId: 'c1' })
    assert.deepEqual(withSelf.posted, [])
  } finally {
    withSelf.restore()
  }
})

test('payloads carry exactly the documented keys and no client data', () => {
  const env = setup()
  try {
    connected(env)
    cycle({ attentionIds: ['c1'], liveIds: ['c1'], newArrivalId: 'c1' })
    reportPresence(false)
    env.fire({ source: 'bbc-crm', type: 'crm:hello' }, CRM)

    const byType = Object.fromEntries(env.posted.map((p) => [p.msg.type, Object.keys(p.msg).sort()]))
    assert.deepEqual(byType['chat:hello'], ['queued', 'source', 'type', 'unread'])
    assert.deepEqual(byType['chat:incoming'], ['conversationId', 'source', 'type', 'unread'])
    assert.deepEqual(byType['chat:presence'], ['online', 'ready', 'source', 'type'])

    const blob = JSON.stringify(env.posted)
    for (const forbidden of ['preview', 'name', 'email', 'phone', 'message', 'text']) {
      assert.ok(!blob.includes(forbidden), `payload must not carry ${forbidden}`)
    }
  } finally {
    env.restore()
  }
})

test('presence is sent only when it actually changes', () => {
  const env = setup()
  try {
    connected(env)
    reportPresence(true)
    reportPresence(true)
    reportPresence(false)

    assert.deepEqual(
      env.posted.map((p) => p.msg.ready),
      [true, false]
    )
  } finally {
    env.restore()
  }
})

// ── Failure modes ─────────────────────────────────────────────────

test('a throwing postMessage is warned about, not swallowed, and never propagates', () => {
  const env = setup({ referrer: CRM, throwOnPost: true })
  try {
    installCrmBridge(deps)
    cycle({ attentionIds: ['c1'], liveIds: ['c1'], newArrivalId: 'c1' })
    assert.equal(env.warnings.length, 1)
    assert.match(String(env.warnings[0][0]), /crm-bridge/)
  } finally {
    env.restore()
  }
})

test('no handshake and no usable referrer: nothing is sent and nothing throws', () => {
  const env = setup({ referrer: '' })
  try {
    installCrmBridge(deps)
    cycle({ attentionIds: ['c1'], liveIds: ['c1'], newArrivalId: 'c1' })
    reportPresence(true)
    assert.deepEqual(env.posted, [], 'a panel that cannot identify its parent stays quiet')
  } finally {
    env.restore()
  }
})

test('a referrer from a foreign origin is not a licence to post', () => {
  const env = setup({ referrer: `${EVIL}/dashboard` })
  try {
    installCrmBridge(deps)
    cycle({ attentionIds: ['c1'], liveIds: ['c1'], newArrivalId: 'c1' })
    assert.deepEqual(env.posted, [])
  } finally {
    env.restore()
  }
})

test('cleanup removes the listener', () => {
  const env = setup()
  try {
    const cleanup = installCrmBridge(deps)
    assert.equal(env.listenerCount(), 1)
    cleanup()
    assert.equal(env.listenerCount(), 0)
    env.fire({ source: 'bbc-crm', type: 'crm:hello' }, CRM)
    assert.deepEqual(env.posted, [])
  } finally {
    env.restore()
  }
})

// ── Source contracts for the half that cannot be imported ─────────

test('the bridge reuses the ONE origin allow-list', () => {
  const bridge = read('src/lib/crm-bridge.ts')
  assert.ok(
    !/buybusinessclass/.test(bridge),
    'crm-bridge.ts must not carry a second list of origins'
  )
  const layout = read('src/components/layout/authenticated-layout.tsx')
  assert.match(layout, /isAllowedOrigin: isAllowedCrmOrigin/)
  assert.match(read('src/lib/crm-embed-auth.ts'), /export function isAllowedCrmOrigin/)
})

test('the heartbeat reports the sets it already computed, and fetches nothing new', () => {
  const src = read('src/hooks/use-heartbeat.ts')
  assert.match(src, /setAttentionIds\(\[\.\.\.needsAttention\]\)/, 'existing write still there')
  assert.match(src, /attentionIds: \[\.\.\.needsAttention\]/)
  assert.match(src, /liveIds: \[\.\.\.liveIds\]/)
  assert.match(src, /c\.status === 'needs_agent'/)
  // Exactly one call site — the pre-existing fetchQuery. The bridge adds no fetch.
  assert.equal((src.match(/getConversations\(/g) ?? []).length, 1)
})

test('the baseline flag is captured before the loop flips it', () => {
  const src = read('src/hooks/use-heartbeat.ts')
  const capture = src.indexOf('const baselineAlreadyTaken = baselineTaken.current')
  const flip = src.indexOf('baselineTaken.current = true')
  assert.ok(capture > 0 && flip > 0, 'both lines must exist')
  assert.ok(
    capture < flip,
    'reading it after the flip would make every baseline cycle look like a normal one'
  )
})

test('presence is reported where readiness is already applied', () => {
  const src = read('src/hooks/use-heartbeat.ts')
  assert.match(src, /setReady\(res\.is_ready\)[\s\S]{0,200}?reportPresence\(res\.is_ready\)/)
})
