/**
 * Security → Bans says what failed.
 *
 * CodeRabbit #228: every failure read "migration 042 may not be applied
 * yet": a sign-in that ran out, a "Forbidden", a network error. The page now
 * shows the error itself, and names 042 only for the server's own read
 * failure, the one case where the table can be what is missing.
 * Runs with: node --experimental-strip-types --test tests/security-bans-view.test.ts
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const page = readFileSync(join(root, 'src/features/security-bans/index.tsx'), 'utf-8')
const route = readFileSync(join(root, '../bbc-chatbot-api/app/api/security_bans.py'), 'utf-8')

test('the error shown is the one that happened', () => {
  const branch = page.slice(page.indexOf('bans.isError ? ('), page.indexOf('No bans recorded.'))
  assert.match(branch, /bans\.error instanceof Error \? bans\.error\.message : 'unknown error'/)
  assert.doesNotMatch(branch, /\(migration 042 may not be applied yet\)/)
})

test('042 is named only for the server read failure, with the server’s own words', () => {
  const words = page.match(/const HISTORY_READ_FAILED = '([^']+)'/)?.[1]
  assert.ok(words, 'the page names the server message it checks')
  assert.ok(route.includes(`"error": "${words}"`), 'the list endpoint says exactly that when its read fails')
  assert.match(page, /bans\.error\.message === HISTORY_READ_FAILED\s*\?\s*' One cause: migration 042 not applied yet\.'/)
})

test('a failed request carries its status and the server’s own detail', () => {
  // CodeRabbit #228 follow-up: listBans turned every non-OK answer into
  // "Failed to load bans", so a 401 read the same as a 5xx.
  const api = readFileSync(join(root, 'src/lib/api.ts'), 'utf-8')
  const fns = api.slice(api.indexOf('export async function listBans'), api.indexOf('export async function unbanBan') + 400)
  assert.match(fns, /apiFetch<AdminEnvelope<SecurityBan\[\]>>\('\/api\/admin\/security\/bans'\)/)
  assert.doesNotMatch(fns, /'Failed to load bans'\)\s*\n\s*const body/)
  assert.doesNotMatch(fns, /if \(!res\.ok\)/)
})

