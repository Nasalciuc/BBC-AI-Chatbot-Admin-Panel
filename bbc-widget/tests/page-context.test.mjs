// The note beside the corner button, by the page the visitor reads (src/pageContext.ts). Alex speaks
// it, and he never checks fares himself: only an agent can (owner, 17 Nov 2026). "I'll check fares"
// promised fares from the bot; the line now says who does it.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { readPageContext } from '../src/pageContext.ts'

const titled = (h1) => {
  globalThis.document = { querySelector: () => (h1 === null ? null : { textContent: h1 }) }
  try { return readPageContext() } finally { delete globalThis.document }
}

const PAGES = [
  'New York to London business class flights',
  'Business class flights to Abu Dhabi',
  'Business class flights from New York',
  'Emirates business class deals',
  'First class flights to London',
  'Some other page',
  null,
]

test('every note says an agent checks the fares, never Alex', () => {
  for (const h1 of PAGES) {
    const { line } = titled(h1)
    assert.doesNotMatch(line, /\bI(?:'ll| will)? check\b/, line)
    assert.match(line, /\ban agent will check\b/, line)
  }
})

test('the cabin of the page stays in the line', () => {
  assert.equal(titled('First class flights to London').line, 'Tell me where from and when, and an agent will check first class fares.')
  assert.equal(titled(null).line, 'Tell me where and when, and an agent will check fares for you.')
})

test('the note the button falls back to says the same', () => {
  const src = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'FloatingButtons.tsx'), 'utf8')
  assert.match(src, /context\?\.line \|\| 'Tell me where and when, and an agent will check fares for you\.'/)
  assert.doesNotMatch(src, /I'll check fares/)
})
