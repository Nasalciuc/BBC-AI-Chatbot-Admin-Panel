/**
 * PR-10 — one request per 5 s for the operator's list, not two.
 *
 * The heartbeat's attention check and the chats page's My Active list must
 * share ONE React Query entry. The key had drifted (five parts vs the list's
 * six), so both fetched the same list every five seconds.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p: string) => readFileSync(join(root, p), 'utf-8')

test('the attention key has the same shape as the list key, at its defaults', () => {
  const hb = read('src/hooks/use-heartbeat.ts')
  const list = read('src/features/chats/index.tsx')
  const keyLine = list.match(/queryKey: \['conversations', ([^\]]+)\]/)
  assert.ok(keyLine, 'list queryKey not found')
  const parts = keyLine[1].split(',').map((s) => s.trim())
  assert.deepEqual(parts, ['activeTab', 'debouncedSearch', 'tunnelFilter', 'handledByFilter', 'tagFilter'])

  const att = hb.match(/ATTENTION_QUERY_KEY = \[([^\]]+)\]/)
  assert.ok(att)
  const attParts = att[1].split(',').map((s) => s.trim().replace(/'/g, ''))
  // conversations + the five list parts at their defaults
  assert.deepEqual(attParts, ['conversations', 'my_active', '', '', 'all', 'all'])
  assert.match(list, /useState<ConversationTag \| 'all'>\('all'\)/)
})

test('both ask for the badges in the same response', () => {
  assert.match(read('src/hooks/use-heartbeat.ts'), /with_counts: 'true'/)
  assert.match(read('src/features/chats/index.tsx'), /with_counts: 'true'/)
  // The standalone counts poll runs only when the list did not carry them —
  // or when no list is fetched at all (the redesign's To read tab).
  assert.match(read('src/features/chats/index.tsx'), /enabled: \(reading \|\| !!convResponse\) && !listCounts/)
})

test('the queue rows refetch when the heartbeat changes the line, not on a timer', () => {
  const q = read('src/features/chats/components/queue-section.tsx')
  assert.doesNotMatch(q, /refetchInterval/)
  assert.match(q, /queryKey: \['queue-rows', queueIds\.join\(','\)\]/)
})

test('a refreshed session token reaches the heartbeat worker', () => {
  const hb = read('src/hooks/use-heartbeat.ts')
  assert.match(hb, /useAuthStore\.subscribe\(/)
  assert.match(hb, /type: 'updateToken'/)
  assert.match(read('public/heartbeat-worker.js'), /msg\.type === 'updateToken'/)
})

test('a failed background refetch does not send the operator to /500', () => {
  const main = read('src/main.tsx')
  assert.match(main, /const hadData = query\.state\.data !== undefined/)
  // Since the redesign no failed query leaves the screen at all (a first
  // load says so where it happened); a refetch toasts once a minute.
  assert.equal(main.indexOf("router.navigate({ to: '/500' })"), -1)
  assert.match(main, /shouldNotifyRefreshFailure\(query\.queryHash, Date\.now\(\)\)/)
})

test('/widget-embed is its own page with only the widget script', () => {
  const html = read('widget-embed.html')
  assert.match(html, /\/widget\/bbc-widget\.js/)
  assert.doesNotMatch(html, /src\/main\.tsx/)
  const vercel = JSON.parse(read('vercel.json'))
  assert.deepEqual(vercel.rewrites[0], { source: '/widget-embed', destination: '/widget-embed.html' })
  assert.ok(vercel.headers.some((h: { source: string }) => h.source === '/widget/(.*)'))
})
