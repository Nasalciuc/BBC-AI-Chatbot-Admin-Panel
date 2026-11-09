/**
 * PR-12 — the widget carries the server-signed visitor token everywhere,
 * refreshes it before it expires, and sends the form's trap.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { tokenExpiry, tokenMatches, needsRefresh } from '../src/api.ts'

const src = (f) => readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'src', f), 'utf8')
const VID = '11111111-2222-3333-4444-555555555555'
const tok = (vid, exp) => `v1.${vid}.${exp - 86400}.${exp}.deadbeef`

test('a token is read for its owner and expiry only', () => {
  assert.equal(tokenExpiry(tok(VID, 2_000_000)), 2_000_000)
  assert.equal(tokenExpiry('garbage'), 0)
  assert.equal(tokenMatches(tok(VID, 1), VID), true)
  assert.equal(tokenMatches(tok('other', 1), VID), false)
})

test('refresh: missing, someone else\'s, or under an hour left', () => {
  const now = 1_000_000
  assert.equal(needsRefresh(null, VID, now), true)
  assert.equal(needsRefresh(tok('other', now + 86400), VID, now), true)
  assert.equal(needsRefresh(tok(VID, now + 1800), VID, now), true)
  assert.equal(needsRefresh(tok(VID, now + 7200), VID, now), false)
  assert.equal(needsRefresh(null, null, now), false)
})

test('every request carries it; SSE in the URL; start/init save it', () => {
  const api = src('api.ts')
  assert.match(api, /headers\.set\('X-Visitor-Token'/)
  const cw = src('ChatWindow.tsx')
  assert.match(cw, /&vt=\$\{encodeURIComponent\(vt\)\}/)
  assert.equal(cw.split('saveVisitorToken(').length - 1, 2)
  assert.doesNotMatch(cw, /await fetch\(`\$\{apiUrl\}\/api\/chat\/(messages|agent-typing)/)
  assert.match(src('Widget.tsx'), /clearVisitorToken\(\)/)
})

test('the form sends its trap and the hidden field stays out of reach', () => {
  const form = src('TunnelForm.tsx')
  assert.match(form, /name="website" tabIndex=\{-1\} autoComplete="off"/)
  assert.match(form, /aria-hidden="true"/)
  assert.equal(form.split('form_check: formCheck()').length - 1, 2)
  assert.match(src('ChatWindow.tsx'), /form_check: formCheck,/)
})

test('Turnstile loads only when the site gives a key', () => {
  const ts = src('turnstile.ts')
  assert.match(ts, /if \(!siteKey\) return null/)
  assert.match(src('entry.tsx'), /data-turnstile-sitekey/)
})

test('the reserved v0 route ships in the bundle and is never called', () => {
  const api = src('api.ts')
  assert.match(api, /export const TRANSCRIPT_EXPORT_V0 = '\/api\/chat\/v0\/transcript-export'/)
  assert.match(src('entry.tsx'), /transcriptExportV0: TRANSCRIPT_EXPORT_V0/)
  for (const f of ['ChatWindow.tsx', 'Widget.tsx', 'TunnelForm.tsx']) {
    assert.doesNotMatch(src(f), /TRANSCRIPT_EXPORT_V0|transcript-export/, f)
  }
})
