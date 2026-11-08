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

test('a script that fails, or loads without a widget, is said on the page', () => {
  // Both listeners go on before the script goes in, and come off when the page goes.
  const listen = hook.indexOf("script.addEventListener('error', onError)")
  assert.ok(hook.indexOf("script.addEventListener('load', onLoad)") > 0 && listen > 0)
  assert.ok(listen < hook.indexOf('document.body.appendChild(script)'), 'listen before appending')
  assert.match(hook, /removeEventListener\('load', onLoad\)/)
  assert.match(hook, /removeEventListener\('error', onError\)/)
  // A missing bundle can come back as the app's HTML: loaded, but no widget ran.
  assert.match(hook, /setState\(document\.getElementById\('bbc-widget-root'\) \? 'ready' : 'failed'\)/)
  for (const page of [embed, standalone]) {
    assert.match(page, /const state = useWidgetScript\(/)
    assert.match(page, /state === 'failed' && <WidgetFailed \/>/)
  }
  assert.match(preview, /state === 'failed' && \(/)
  assert.match(preview, /The widget didn’t load\./)
  assert.match(read('features/widget-embed/widget-failed.tsx'), /role='alert'/)
})

test('leaving a widget page unmounts the widget before its root goes', () => {
  // Removing the root alone runs no cleanup: a phone sheet would keep the page
  // from scrolling, and the chat's stream and timers would keep running.
  const drop = hook.slice(hook.indexOf('function dropWidget()'), hook.indexOf('}', hook.indexOf('function dropWidget()')))
  const teardown = drop.indexOf('window.__BBC_WIDGET_TEARDOWN__?.()')
  assert.ok(teardown > 0 && teardown < drop.indexOf("getElementById('bbc-widget-root')?.remove()"))
  // Before mounting (a widget still there) and when the page goes.
  assert.equal(hook.match(/dropWidget\(\)/g)?.length, 2 + 1, 'definition + the two calls')
  // The window's widget fields are typed once, with the shared types.
  assert.ok(!hook.includes('declare global'))
  assert.match(read('lib/types.ts'), /__BBC_WIDGET_TEARDOWN__\?: \(\) => void/)
})

test("the CRM iframe's own page (/widget-embed.html, v2 PR-10) says so too", () => {
  // In production /widget-embed is this static page (vercel.json), not the app route.
  const html = readFileSync(join(SRC, '..', 'widget-embed.html'), 'utf8')
  const listen = html.indexOf("s.addEventListener('error', fail)")
  assert.ok(listen > 0 && listen < html.indexOf('document.body.appendChild(s)'), 'listen before appending')
  assert.match(html, /if \(!document\.getElementById\('bbc-widget-root'\)\) fail\(\)/)
  assert.match(html, /The chat couldn\\u2019t load\. Reload the page to try again\./)
})

