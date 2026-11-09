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

// CodeRabbit #228: five digits went to the server, came back as a 422 with a
// list for detail, read "Something went wrong", and the field was cleared.
import { MFA_CODE_MIN, mfaCodeProblem } from '../src/lib/mfa-code.ts'

test('a code shorter than the server takes is answered here', () => {
  assert.equal(MFA_CODE_MIN, 6)
  assert.match(mfaCodeProblem('12345', 'required') ?? '', /6-digit code/)
  assert.match(mfaCodeProblem('123 45', 'enroll') ?? '', /6 digits/)
  assert.equal(mfaCodeProblem('123456', 'required'), null)
  assert.equal(mfaCodeProblem('123 456', 'enroll'), null)
  assert.equal(mfaCodeProblem('ab3k9-x7q2m', 'required'), null) // a recovery code
})

test('the step checks before it asks, and keeps what was typed', () => {
  const step = read('src/features/auth/sign-in/components/mfa-step.tsx')
  const submit = step.slice(step.indexOf('async function submit('), step.indexOf('if (recovery) {'))
  const check = submit.indexOf('const problem = mfaCodeProblem(code, mode)')
  assert.ok(check > 0 && check < submit.indexOf('setBusy(true)'), 'checked before any request')
  const branch = submit.slice(check, submit.indexOf('setBusy(true)'))
  assert.match(branch, /setError\(problem\)\s*return/)
  assert.doesNotMatch(branch, /setCode\(''\)/)
})
