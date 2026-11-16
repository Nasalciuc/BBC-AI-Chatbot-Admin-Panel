/**
 * The chat over the whole screen, with bigger text, on computers (owner's request, 16 Nov: many clients
 * are over 70). One switch (src/largeView.ts) for the form and the chat, set from the panel's top,
 * remembered on the browser, never on phones (full-screen already) and never in the CRM iframe.
 *
 * Plain .mjs + strip-types import: the widget's zero-dep test pattern.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { LARGE_KEY, isLarge, readLarge, setLarge } from '../src/largeView.ts'

const src = (f) => readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'src', f), 'utf8')
const top = src('PanelTop.tsx')
const chat = src('ChatWindow.tsx')
const form = src('TunnelForm.tsx')
const css = src('styles.ts')

function fakeStorage() {
  const m = new Map()
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => { m.set(k, String(v)) },
    removeItem: (k) => { m.delete(k) },
  }
}

test('the choice is remembered on this browser and taken back', () => {
  globalThis.localStorage = fakeStorage()
  try {
    assert.equal(readLarge(), false)
    setLarge(true)
    assert.equal(isLarge(), true)
    assert.equal(localStorage.getItem(LARGE_KEY), '1')
    assert.equal(readLarge(), true)
    setLarge(false)
    assert.equal(isLarge(), false)
    assert.equal(localStorage.getItem(LARGE_KEY), null)
  } finally {
    delete globalThis.localStorage
  }
})

test('storage blocked: the usual panel, and the switch still works for the page', () => {
  globalThis.localStorage = {
    getItem: () => { throw new Error('blocked') },
    setItem: () => { throw new Error('blocked') },
    removeItem: () => { throw new Error('blocked') },
  }
  try {
    assert.equal(readLarge(), false)
    assert.doesNotThrow(() => setLarge(true))
    assert.equal(isLarge(), true)
    setLarge(false)
  } finally {
    delete globalThis.localStorage
  }
})

test('the button in the top says what it does, and is not drawn in the CRM iframe', () => {
  assert.match(top, /canEnlarge && \(/)
  assert.match(top, /'Make the chat smaller' : 'Make the chat bigger'/)
  assert.match(top, /aria-label=\{sizeLabel\}/)
  assert.doesNotMatch(top, /aria-pressed=/)
  assert.match(top, /posthog\?\.capture\('widget_large_view', \{ on: !large \}\)/)
  for (const panel of [chat, form]) {
    assert.match(panel, /canEnlarge=\{!embedded\}/)
    assert.match(panel, /\$\{large && !embedded \? ' bw-panel--large' : ''\}/)
  }
})

/** Every top-level `@media` block of the stylesheet: its condition and where it starts and ends. */
function mediaBlocks(text) {
  const out = []
  let at = text.indexOf('@media ')
  while (at > -1) {
    const open = text.indexOf('{', at)
    let depth = 0
    let i = open
    for (; i < text.length; i++) {
      if (text[i] === '{') depth++
      else if (text[i] === '}' && --depth === 0) break
    }
    out.push({ cond: text.slice(at + 7, open).trim(), start: at, end: i })
    at = text.indexOf('@media ', i)
  }
  return out
}

test('computers only: the big view lives under min-width 768px, the button is hidden on phones', () => {
  const blocks = mediaBlocks(css)
  const main = blocks.find(b => b.cond === '(min-width: 768px)' && css.slice(b.start, b.end).includes('.bw-panel.bw-panel--large {'))
  assert.ok(main)
  const block = css.slice(main.start, main.end)
  assert.match(block, /top: 0; left: 0; right: 0; bottom: 0; z-index: 2147483001;/)
  assert.match(block, /\.bw-panel--large \.bw-msg \{[^}]*font-size: 20px/)
  // Nowhere else: every big rule sits in a block for computers (the short-screen ones included), so a
  // phone never gets one.
  const forComputers = blocks.filter(b => b.cond.startsWith('(min-width: 768px)'))
  let at = css.indexOf('.bw-panel--large')
  assert.ok(at > -1)
  while (at > -1) {
    assert.ok(forComputers.some(b => b.start < at && at < b.end), `a big rule outside the computer blocks, at ${at}`)
    at = css.indexOf('.bw-panel--large', at + 1)
  }
  assert.match(css, /@media \(max-width: 767px\) \{\s*#bbc-widget-root \.bw-iconbtn--size \{ display: none; \}/)
})
