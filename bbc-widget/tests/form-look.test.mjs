/**
 * The contact form, redrawn for older eyes (owner's request, 16 Nov: many clients are over 70): big type,
 * tall fields with strong edges, the label above each field, the example under it, readable errors and
 * the number to call. The chat's own composer keeps its size.
 *
 * Plain .mjs + strip-types import: the widget's zero-dep test pattern.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const src = (f) => readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'src', f), 'utf8')
const form = src('TunnelForm.tsx')
const css = src('styles.ts')

test('the small chat keeps its composer: the form\'s bigger field does not leak into it', () => {
  assert.match(css, /\.bw-compose \.bw-input \{[^}]*font-size: 16px;/)
})

test('the form for older eyes: big type, tall fields, the number to call', () => {
  assert.match(css, /\.bw-label \{ font-size: 17px; font-weight: 700;/)
  assert.match(css, /\.bw-input \{\s*display: block; width: 100%; height: 56px;[^}]*font-size: 18px;/)
  assert.match(css, /\.bw-error \{[^}]*font-size: 16px; font-weight: 600;/)
  assert.match(css, /\.bw-cta \{[^}]*height: 60px;[^}]*font-size: 19px;/)
  assert.match(form, /class="bw-form__call"/)
  assert.match(form, /entry_point: 'widget_form'/)
})

test('a flagged field still shows where the keyboard is, with one ring on the number (CodeRabbit #256)', () => {
  // A failed send moves the focus to the first flagged field; its red ring alone looks the same either way.
  const field = css.indexOf('.bw-input[aria-invalid="true"]:focus-visible { outline: 2px solid var(--bw-danger); outline-offset: 2px; }')
  assert.ok(field > css.indexOf('.bw-input[aria-invalid="true"] {'), 'after the flagged look, so it adds to it')
  assert.match(css, /\.bw-phone\[data-invalid="true"\]:focus-within \{ outline: 2px solid var\(--bw-danger\); outline-offset: 2px; \}/)
  // The field inside the number's box draws no ring of its own: the same weight, later, so it wins.
  const inner = css.indexOf('.bw-phone .bw-input:focus-visible { outline: none; }')
  assert.ok(field > -1 && inner > field)
})

/** The text of the top-level `@media <cond> {` block. */
function media(cond) {
  const at = css.indexOf(`@media ${cond} {`)
  assert.ok(at > -1, cond)
  let depth = 0
  for (let i = css.indexOf('{', at); i < css.length; i++) {
    if (css[i] === '{') depth++
    else if (css[i] === '}' && --depth === 0) return css.slice(at, i + 1)
  }
  throw new Error(`unclosed ${cond}`)
}

test('on a short laptop page the button and the number to call stay in sight; the fields scroll above them', () => {
  // A 1366×768 laptop leaves the page about 620-660 px: the button used to fall below the edge.
  const fields = form.indexOf('<div class="bw-form__fields">')
  const foot = form.indexOf('<div class="bw-form__foot">')
  assert.ok(fields > -1 && foot > fields)
  assert.ok(form.indexOf("{tunnel === 'sales' ? (") > fields && form.indexOf("{tunnel === 'sales' ? (") < foot)
  assert.ok(form.indexOf('class="bw-cta"') > foot && form.indexOf('class="bw-form__call"') > foot)
  // Spaced like the form itself, so the phone sheet and the tall-screen panel look as they did.
  assert.match(css, /\.bw-form__fields,\s*#bbc-widget-root \.bw-form__foot \{ display: flex; flex-direction: column; gap: inherit; \}/)
  const desk = media('(min-width: 768px)')
  assert.match(desk, /\.bw-panel--form \.bw-form \{ overflow: hidden; \}/)
  assert.match(desk, /\.bw-panel--form \.bw-form__fields \{\s*flex: 0 1 auto; min-height: 0; overflow-y: auto;/)
  assert.match(desk, /\.bw-panel--form \.bw-form__foot \{ flex-shrink: 0; \}/)
  // A field reached with the keyboard comes into view with its label and its whole focus ring.
  assert.match(desk, /\.bw-form__fields \.bw-input \{ scroll-margin: 36px 0 10px; \}/)
  // The whole-screen form too: its card stops at the screen's bottom, the button in it.
  assert.match(css, /\.bw-panel--large \.bw-form \{\s*flex: 0 1 auto; min-height: 0; overflow: hidden;/)
  // Phones keep their own way: the whole sheet scrolls.
  assert.match(css, /\.bw-panel--form \.bw-form \{ flex: 0 0 auto; overflow: visible;/)
})

test('a short screen shrinks the space and drops the line under the title, never the fields or their type', () => {
  const short = [
    '(min-width: 768px) and (max-height: 840px)', '(min-width: 768px) and (max-height: 760px)',
    '(min-width: 768px) and (max-height: 640px)', '(max-width: 767px) and (max-height: 760px)',
  ]
  for (const cond of short) {
    assert.doesNotMatch(media(cond), /\.bw-(input|label|cta|phone|hint|error)\b[^{]*\{[^}]*(font-size|height)/, cond)
  }
  assert.match(media('(min-width: 768px) and (max-height: 760px)'), /\.bw-panel--form \.bw-intro__lead \{ display: none; \}/)
  // Phones (most show 560-760 px of page): the button on the first screen; the rules are the form's
  // only, so the chat is untouched, and they come before the smallest phones' tighter ones.
  const phone = media('(max-width: 767px) and (max-height: 760px)')
  assert.match(phone, /\.bw-panel--form \.bw-intro__lead \{ display: none; \}/)
  for (const rule of phone.split('\n').filter(l => l.includes('{') && !l.startsWith('@media'))) {
    assert.match(rule, /^\s*#bbc-widget-root \.bw-panel--form /, rule)
  }
  assert.ok(css.indexOf('@media (max-width: 767px) and (max-height: 760px)') < css.indexOf('@media (max-width: 767px) and (max-height: 620px)'))
})
