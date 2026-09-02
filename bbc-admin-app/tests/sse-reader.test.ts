/**
 * The panel listens instead of asking.
 *
 * Three polls per open chat — typing every 500ms, messages and presence every
 * 2s — were asking about events the server already knew. This reader is what
 * replaces them, and it has two jobs it must never get wrong: the token goes
 * in a HEADER (a session JWT in a query string lands in every proxy log), and
 * when it gives up the polls come back at exactly today's intervals.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

import { openSse } from '../src/lib/sse-reader.ts'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p: string) => readFileSync(join(root, p), 'utf-8')

const enc = new TextEncoder()

/** A Response whose body streams the given chunks, then ends. */
function streamOf(chunks: string[]) {
  let i = 0
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () =>
          i < chunks.length
            ? { value: enc.encode(chunks[i++]), done: false }
            : { value: undefined, done: true },
      }),
    },
  }
}

function withFetch(impl: (url: string, init: any) => Promise<any>) {
  const real = (globalThis as any).fetch
  const calls: Array<{ url: string; init: any }> = []
  ;(globalThis as any).fetch = async (url: string, init: any) => {
    calls.push({ url, init })
    return impl(url, init)
  }
  return { calls, restore: () => { (globalThis as any).fetch = real } }
}

const settle = () => new Promise((r) => setTimeout(r, 0))

test('the token travels in the Authorization header, never in the URL', async () => {
  const f = withFetch(async () => streamOf([]))
  try {
    const h = openSse(
      'https://api.test/api/agent/stream/c1',
      { Authorization: 'Bearer JWT123' },
      () => {},
      () => {},
      () => {},
    )
    await settle()
    h.close()
    assert.equal(f.calls.length, 1)
    assert.doesNotMatch(f.calls[0].url, /JWT123/, 'a JWT in the URL lands in every proxy log')
    assert.equal(f.calls[0].init.headers.Authorization, 'Bearer JWT123')
    assert.equal(f.calls[0].init.headers.Accept, 'text/event-stream')
  } finally {
    f.restore()
  }
})

test('two frames arriving in one chunk are both delivered', async () => {
  const f = withFetch(async () =>
    streamOf(['data: {"id":"m1"}\n\ndata: {"id":"m2"}\n\n'])
  )
  const seen: any[] = []
  try {
    const h = openSse('u', {}, (e) => seen.push(e), () => {}, () => {})
    await settle()
    h.close()
    assert.deepEqual(seen, [{ id: 'm1' }, { id: 'm2' }])
  } finally {
    f.restore()
  }
})

test('a frame split across chunks is reassembled', async () => {
  const f = withFetch(async () => streamOf(['data: {"id":', '"m3"}\n\n']))
  const seen: any[] = []
  try {
    const h = openSse('u', {}, (e) => seen.push(e), () => {}, () => {})
    await settle()
    h.close()
    assert.deepEqual(seen, [{ id: 'm3' }])
  } finally {
    f.restore()
  }
})

test('keepalive comments are not events', async () => {
  const f = withFetch(async () => streamOf([': keepalive\n\n', 'data: {"id":"m4"}\n\n']))
  const seen: any[] = []
  try {
    const h = openSse('u', {}, (e) => seen.push(e), () => {}, () => {})
    await settle()
    h.close()
    assert.deepEqual(seen, [{ id: 'm4' }])
  } finally {
    f.restore()
  }
})

test('an unparseable frame costs one event, not the stream', async () => {
  const f = withFetch(async () => streamOf(['data: not json\n\n', 'data: {"id":"m5"}\n\n']))
  const seen: any[] = []
  try {
    const h = openSse('u', {}, (e) => seen.push(e), () => {}, () => {})
    await settle()
    h.close()
    assert.deepEqual(seen, [{ id: 'm5' }])
  } finally {
    f.restore()
  }
})

test('it gives up after three consecutive failures so polling can resume', async () => {
  const f = withFetch(async () => ({ ok: false, status: 503, body: null }))
  const states: string[] = []
  let gaveUp = 0
  try {
    openSse('u', {}, () => {}, (s) => states.push(s), () => { gaveUp += 1 })
    // 1s + 2s of backoff between the three attempts.
    await new Promise((r) => setTimeout(r, 3_500))
    assert.equal(f.calls.length, 3, 'three attempts, then it stops trying')
    assert.equal(gaveUp, 1)
    assert.equal(states.at(-1), 'closed')
    assert.deepEqual(states.slice(0, 2), ['reconnecting', 'reconnecting'])
  } finally {
    f.restore()
  }
})

test('close() stops the loop and never gives up on the caller', async () => {
  const f = withFetch(async () => ({ ok: false, status: 500, body: null }))
  let gaveUp = 0
  try {
    const h = openSse('u', {}, () => {}, () => {}, () => { gaveUp += 1 })
    h.close()
    await new Promise((r) => setTimeout(r, 1_200))
    assert.equal(gaveUp, 0, 'a deliberate close is not a failure')
  } finally {
    f.restore()
  }
})

// ── Source contract: the panel wiring cannot be imported (Vite aliases) ──

test('the three polls stop while the stream is open, and return unchanged', () => {
  const src = read('src/features/chats/detail.tsx')
  const lines = src.split('\n').filter((l) => l.includes('refetchInterval:'))
  assert.equal(lines.length, 3)
  for (const l of lines) {
    assert.match(l, /dormant \|\| sseLive \? false :/)
  }
  // The fallback values are exactly what they were before SSE existed.
  assert.match(src, /activeTab === 'my_active' \? 500 : false/)
  assert.equal(
    src.split("activeTab === 'my_active' ? 2_000 : false").length - 1,
    2,
  )
})

test('the panel subscribes to its own conversation and cleans up', () => {
  const src = read('src/features/chats/detail.tsx')
  assert.match(src, /openAgentStream\(/)
  assert.doesNotMatch(src, /from '@\/lib\/sse-reader'/)
  assert.doesNotMatch(src, /accessToken/)
  assert.match(src, /setSseLive\(state === 'open'\)/)
  assert.match(src, /return \(\) => h\.close\(\)/)
  // stream_chunk belongs to the widget's typewriter, not the panel.
  assert.match(src, /case 'stream_chunk':/)
  // Messages are deduped: the incremental poll may have delivered the row.
  assert.match(src, /\(old \?\? \[\]\)\.some\(\(m\) => m\.id === e\.id\)/)
})

test('the reader is transport: it never sees a token', () => {
  const src = read('src/lib/sse-reader.ts')
  assert.match(src, /headers: \{ \.\.\.headers, Accept: 'text\/event-stream' \}/)
  assert.doesNotMatch(src, /\btoken\b/)
  assert.doesNotMatch(src, /new EventSource/)
})

test('openAgentStream owns auth via authHeaders and validates frames', () => {
  const src = read('src/lib/api.ts')
  assert.match(src, /export function openAgentStream/)
  assert.match(src, /authHeaders\(\)/)
  assert.match(src, /parseAgentSseEvent\(raw\)/)
  assert.match(src, /\$\{BASE\}\/api\/agent\/stream\/\$\{conversationId\}/)
})

test('the stream does not open for a dormant panel or a mock view', () => {
  const src = read('src/features/chats/detail.tsx')
  assert.match(
    src,
    /if \(!conversationId \|\| activeTab !== 'my_active' \|\| dormant \|\| usingMock\) return/,
  )
  assert.match(src, /\}, \[conversationId, activeTab, dormant, usingMock, queryClient\]\)/)
})

test('features/ never imports sse-reader and never reads the access token', () => {
  const feat = join(root, 'src/features')
  const walk = (dir: string): string[] =>
    readdirSync(dir, { withFileTypes: true }).flatMap((d) =>
      d.isDirectory() ? walk(join(dir, d.name)) : [join(dir, d.name)],
    )
  for (const file of walk(feat).filter((f) => f.endsWith('.ts') || f.endsWith('.tsx'))) {
    const src = readFileSync(file, 'utf-8')
    assert.doesNotMatch(src, /from '@\/lib\/sse-reader'/, file)
    assert.doesNotMatch(src, /accessToken/, file)
  }
})
