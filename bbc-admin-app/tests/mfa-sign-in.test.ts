/**
 * PR-13 — a privileged sign-in finishes with a second factor. The panel must
 * never store the short-lived mfa_token as the session, and the QR the server
 * sends is shown as an image (an <img> SVG cannot run script).
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p: string) => readFileSync(join(root, p), 'utf-8')

test('the password step hands over to the second factor instead of logging in', () => {
  // The redesigned sign-in page (#240) replaced user-auth-form.tsx.
  const form = read('src/features/auth/sign-in/index.tsx')
  const i = form.indexOf('if (res.mfa && res.mfa_token)')
  const j = form.indexOf('finishLogin(res.token, res.user)')
  assert.ok(i > 0 && j > i, 'the mfa branch must run before the session is stored')
  assert.doesNotMatch(form, /setAccessToken\(res\.mfa_token/)
})

test('the QR is an image, never injected HTML', () => {
  const step = read('src/features/auth/sign-in/components/mfa-step.tsx')
  assert.match(step, /src=\{`data:image\/svg\+xml;utf8,\$\{encodeURIComponent\(qr\.qr_svg\)\}`\}/)
  assert.doesNotMatch(step, /dangerouslySetInnerHTML/)
})

test('recovery codes are shown once, before the session starts', () => {
  const step = read('src/features/auth/sign-in/components/mfa-step.tsx')
  assert.match(step, /They will not be shown again/)
  assert.match(step, /onClick=\{\(\) => onDone\(recovery\.token, recovery\.user\)\}/)
})
