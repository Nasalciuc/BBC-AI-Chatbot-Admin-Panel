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
