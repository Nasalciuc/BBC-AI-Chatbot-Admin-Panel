// Each chat keeps its own draft — pinned on lib/chat-drafts.ts and the composer.
// Runs with: node --experimental-strip-types --test tests/chat-drafts.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { DRAFT_PREFIX, clearDrafts, readDraft, writeDraft } from '../src/lib/chat-drafts.ts'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function memory() {
  const m = new Map<string, string>()
  return {
    m,
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
  }
}

test('a draft belongs to one chat', () => {
  const s = memory()
  writeDraft('a', 'Reply for David', s)
  assert.equal(readDraft('a', s), 'Reply for David')
  assert.equal(readDraft('b', s), '')
  assert.equal(s.m.get(DRAFT_PREFIX + 'a'), 'Reply for David')
})

test('sending (an empty composer) removes it; no storage means no crash', () => {
  const s = memory()
  writeDraft('a', 'x', s)
  writeDraft('a', '   ', s)
  assert.equal(s.m.size, 0)
  writeDraft('a', 'x', null)
  assert.equal(readDraft('a', null), '')
  const broken = { getItem: () => { throw new Error('blocked') }, setItem: () => { throw new Error('quota') }, removeItem: () => {} }
  assert.equal(readDraft('a', broken), '')
  assert.doesNotThrow(() => writeDraft('a', 'x', broken))
})

test('the composer loads the draft of the chat it shows, and saves as you type', () => {
  const src = readFileSync(join(root, 'src/features/chats/detail.tsx'), 'utf-8')
  // Opening another chat swaps the text: a reply meant for one client never
  // follows you into another chat (it used to: one useState for every chat).
  assert.match(src, /useState\(\(\) => readDraft\(conversationId\)\)/)
  assert.match(src, /if \(draftOf !== conversationId\) \{\s*setDraftOf\(conversationId\)\s*setInput\(readDraft\(conversationId\)\)/)
  assert.match(src, /writeDraft\(conversationId, input\)\s*\}, \[conversationId, input\]\)/)
  assert.ok(!/useState\(''\)\s*\n/.test(src.slice(src.indexOf('const [input, setInput]'), src.indexOf('const [input, setInput]') + 80)))
})

test('signing out on purpose clears every draft, and nothing else', () => {
  const m = new Map<string, string>([[DRAFT_PREFIX + 'a', 'x'], [DRAFT_PREFIX + 'b', 'y'], ['bbc_idle_nudge_seen', '1']])
  const s = {
    get length() { return m.size },
    key: (i: number) => [...m.keys()][i] ?? null,
    getItem: (k: string) => m.get(k) ?? null,
    setItem: (k: string, v: string) => void m.set(k, v),
    removeItem: (k: string) => void m.delete(k),
  }
  clearDrafts(s)
  assert.deepEqual([...m.keys()], ['bbc_idle_nudge_seen'])
  const dialog = readFileSync(join(root, 'src/components/sign-out-dialog.tsx'), 'utf-8')
  assert.ok(dialog.indexOf('clearDrafts()') < dialog.indexOf('auth.reset()'))
})
