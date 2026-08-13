/**
 * Composer focus retention — the operator's cursor comes back.
 *
 * `disabled={sending}` on the textarea drops browser focus the moment
 * Enter fires; nothing ever re-focused it, so after every send (and
 * worse, after every FAILED send, where the preserved input waits for a
 * re-press) the operator typed into the void.
 *
 * Note: the repo has no RTL/jsdom (zero-dep node:test policy), so these
 * are source contracts on the exact behaviors an RTL test would assert:
 * refocus in finally (covers success AND error), input preserved on
 * error, double-Enter guarded, rAF used (focus on a still-disabled
 * textarea is a no-op).
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const src = readFileSync(join(root, 'src/features/chats/detail.tsx'), 'utf-8')

const handleSend = src.slice(
  src.indexOf('const handleSend = async ()'),
  src.indexOf('const QUICK_EMOJIS'),
)

test('finally re-enables AND refocuses via rAF — success and error alike', () => {
  const finallyBlock = handleSend.slice(handleSend.indexOf('} finally {'))
  assert.match(finallyBlock, /setSending\(false\)/)
  assert.match(
    finallyBlock,
    /requestAnimationFrame\(\(\) => taRef\.current\?\.focus\(\)\)/,
    'refocus must live in finally (the error path needs it MOST: preserved input awaits a re-press)',
  )
  // rAF ordering: re-enable first, then focus on the next frame.
  assert.ok(
    finallyBlock.indexOf('setSending(false)') <
      finallyBlock.indexOf('requestAnimationFrame'),
  )
})

test('input preserved on error — cleared only on success', () => {
  const tryBlock = handleSend.slice(
    handleSend.indexOf('try {'),
    handleSend.indexOf('} catch'),
  )
  const catchBlock = handleSend.slice(
    handleSend.indexOf('} catch'),
    handleSend.indexOf('} finally'),
  )
  assert.match(tryBlock, /setInput\(''\)/)
  assert.ok(!catchBlock.includes("setInput('')"), 'error path must keep the text')
})

test('double-Enter while sending → single send (guard clause)', () => {
  assert.match(handleSend, /if \(!input\.trim\(\) \|\| sending\) return/)
})

test('the cause stays documented: textarea disables while sending', () => {
  assert.match(src, /disabled=\{sending\}/)
  // Enter is fully intercepted — no native behavior rescues focus.
  assert.match(src, /e\.preventDefault\(\); handleSend\(\)/)
})
