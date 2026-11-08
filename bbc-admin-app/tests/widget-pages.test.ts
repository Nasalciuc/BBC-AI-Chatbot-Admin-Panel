// The panel's widget pages show the real widget, never a copy of it.
// Runs with: node --experimental-strip-types --test tests/widget-pages.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const read = (p: string) => readFileSync(join(SRC, p), 'utf8')

const hook = read('features/widget-embed/use-widget-script.ts')
const embed = read('features/widget-embed/index.tsx')
const preview = read('features/widget-preview/index.tsx')
const standalone = read('features/chat-standalone/index.tsx')

test('one loader: the bundle built from bbc-widget, with the API address', () => {
  assert.match(hook, /script\.src = '\/widget\/bbc-widget\.js'/)
  assert.match(hook, /setAttribute\('data-api', API_URL\)/)
  assert.match(hook, /import\.meta\.env\.VITE_API_URL/)
  // Embedded only when asked: the site behaviour (corner button) is the default.
  assert.match(hook, /if \(embedded\) script\.setAttribute\('data-mode', 'embedded'\)/)
})

test('the CRM iframe keeps its switch: /widget-embed?embedded=1', () => {
  assert.match(embed, /useWidgetScript\(\{\s*embedded: new URLSearchParams\(window\.location\.search\)\.get\('embedded'\) === '1',?\s*\}\)/)
})

test('/widget-preview shows the real widget with its corner button', () => {
  assert.match(preview, /useWidgetScript\(\{ embedded: false \}\)/)
  assert.match(preview, /reaches the operators/)
})

test('/chat/sales and /chat/support open the real widget as a full page', () => {
  assert.match(standalone, /useWidgetScript\(\{ embedded: true \}\)/)
})

test('the old copy of the widget is gone and nothing imports it', () => {
  assert.ok(!existsSync(join(SRC, 'features/widget-preview/components/bbc-chat-widget.tsx')))
  assert.ok(!existsSync(join(SRC, 'features/widget-preview/embed.tsx')))
  for (const page of [embed, preview, standalone]) {
    assert.ok(!page.includes('BBCChatWidget'))
    assert.ok(!page.includes('bbc-chat-widget'))
  }
})
