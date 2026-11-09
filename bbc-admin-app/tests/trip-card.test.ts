/**
 * The trip card ("Trip details so far", v2 PR-7) shows the trip as it is now.
 *
 * CodeRabbit #227: the card's query never refetched (staleTime: Infinity), and
 * a lead_details push sent while the chat's stream was shut (another chat
 * open, a dropped connection) was lost, so coming back showed an old
 * "Missing" list and suggested a question the client had answered. And a
 * failed first load returned null, which the cache kept as "no trip".
 *
 * Source contracts (no jsdom in this repo): the card is read again each time
 * the stream opens, its load throws on a failed request, a 403 is retried once.
 * Runs with: node --experimental-strip-types --test tests/trip-card.test.ts
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const detail = readFileSync(join(root, 'src/features/chats/detail.tsx'), 'utf-8')
const api = readFileSync(join(root, 'src/lib/api.ts'), 'utf-8')

test('each time the stream opens, the card is read again', () => {
  const stream = detail.slice(detail.indexOf('const h = openAgentStream('), detail.indexOf('return () => h.close()'))
  const onState = stream.slice(stream.indexOf('(state) => {'))
  assert.match(onState, /setSseLive\(state === 'open'\)/)
  assert.match(
    onState,
    /if \(state === 'open'\) \{\s*void queryClient\.invalidateQueries\(\{ queryKey: \['conversation-details', conversationId\] \}\)/,
  )
})

test('a push still wins over a load in flight', () => {
  const push = detail.slice(detail.indexOf("case 'lead_details': {"), detail.indexOf("case 'stream_end': {"))
  assert.match(push, /\.cancelQueries\(\{ queryKey: key \}\)\s*\.finally\(\(\) => queryClient\.setQueryData\(key, details as LeadDetails\)\)/)
})

test('the card query: fresh while open, no refetch on focus, one retry', () => {
  const q = detail.slice(detail.indexOf('useQuery<LeadDetails | null>({'))
  const opts = q.slice(0, q.indexOf('\n  })'))
  assert.match(opts, /queryKey: \['conversation-details', conversationId\]/)
  assert.match(opts, /staleTime: Infinity/)
  assert.match(opts, /retry: 1/)
})

test('a failed first load throws instead of caching "no trip"', () => {
  const fn = api.slice(api.indexOf('export async function getConversationDetails'), api.indexOf('export async function loginUser'))
  assert.match(fn, /apiFetch<AdminEnvelope<LeadDetails \| null>>\(/)
  assert.doesNotMatch(fn, /return null/)
  assert.doesNotMatch(fn, /\bfetch\(/)
})
