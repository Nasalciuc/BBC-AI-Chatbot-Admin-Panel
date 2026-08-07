// First-contact truth: the widget never fakes a client message, opens via
// /api/chat/start, and re-marks presence on bfcache returns (pageshow).
// Static source-contract tests — zero dependencies (node --test).

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')

function allSourceFiles(dir) {
  const out = []
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) out.push(...allSourceFiles(p))
    else if (/\.(tsx?|jsx?|mjs)$/.test(name)) out.push(p)
  }
  return out
}

const chatWindow = readFileSync(join(SRC, 'ChatWindow.tsx'), 'utf8')

test('no canned client greeting remains anywhere in src/', () => {
  for (const file of allSourceFiles(SRC)) {
    const text = readFileSync(file, 'utf8')
    assert.ok(
      !text.includes('looking for business class'),
      `fake sales greeting survives in ${file}`,
    )
    assert.ok(
      !text.includes('I need help with my booking'),
      `fake support greeting survives in ${file}`,
    )
  }
})

test('fresh sessions open through /api/chat/start', () => {
  assert.match(chatWindow, /\/api\/chat\/start/)
  // The mount effect starts the conversation instead of sending a message,
  // and restored sessions keep the skip.
  assert.match(chatWindow, /if \(savedConvId\) return\s+startConversation\(\)/)
})

test('the greeting renders as an AI bubble from the /start response', () => {
  assert.match(chatWindow, /startConversation/)
  assert.match(chatWindow, /role: 'ai'/)
})

test('pageshow re-opens the session after bfcache return', () => {
  assert.match(chatWindow, /addEventListener\('pageshow', handlePageReturn\)/)
  assert.match(chatWindow, /removeEventListener\('pageshow', handlePageReturn\)/)
  // Re-arms the close beacon so a later pagehide can fire again.
  assert.match(chatWindow, /closeSentRef\.current = false\s+apiFetch\(`\$\{apiUrl\}\/api\/chat\/session\/\$\{convId\}\/open`/)
})

test('pendingGreeting path also uses /start (no canned resend)', () => {
  const pendingBlock = chatWindow.slice(chatWindow.indexOf('pendingGreeting || convId'))
  assert.ok(pendingBlock.slice(0, 300).includes('startConversation()'))
})
