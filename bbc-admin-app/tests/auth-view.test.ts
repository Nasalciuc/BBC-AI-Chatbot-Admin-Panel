// What the sign-in screens say — pinned on features/auth/view.ts.
// Runs with: node --experimental-strip-types --test tests/auth-view.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  BURST_PAUSE_S,
  LINK_TEXT,
  backAfterSignIn,
  crmEmail,
  crmProblem,
  emailError,
  emailOk,
  linkProblem,
  passwordRules,
  problemText,
  signInProblem,
} from '../src/features/auth/view.ts'

test('every answer of POST /api/auth/login has its own words', () => {
  // app/api/auth_routes.py login(): the server's exact words.
  assert.equal(signInProblem(401, 'Invalid email or password').kind, 'wrong')
  assert.equal(signInProblem(401, 'Account not activated — contact admin').kind, 'not_set_up')
  assert.equal(signInProblem(403, 'Account disabled — contact admin').kind, 'off')
  // app/security/rate_limiter.py: a burst, the hourly cap, the daily cap.
  assert.deepEqual(signInProblem(429, 'Too many requests. Please slow down.'), { kind: 'locked', wait: 'seconds' })
  assert.deepEqual(signInProblem(429, 'Hourly message limit reached.'), { kind: 'locked', wait: 'hour' })
  assert.deepEqual(signInProblem(429, 'Daily message limit reached.'), { kind: 'locked', wait: 'day' })
  assert.equal(signInProblem(null, '').kind, 'unreachable')
  assert.equal(signInProblem(500, 'boom').kind, 'unreachable')
  assert.equal(problemText({ kind: 'locked', wait: 'seconds' }).text, 'Wait a few seconds and try again.')
  assert.equal(problemText({ kind: 'locked', wait: 'hour' }).text, 'Sign in is paused for up to an hour.')
  assert.match(problemText({ kind: 'locked', wait: 'day' }).text, /until tomorrow/)
  assert.equal(problemText({ kind: 'wrong' }).head, 'That e-mail and password don’t match.')
  assert.equal(BURST_PAUSE_S, 15)
})

test('the e-mail check says what is wrong before anything is sent', () => {
  assert.equal(emailError(''), 'Type your e-mail.')
  assert.equal(emailError('   '), 'Type your e-mail.')
  assert.equal(emailError('ana@'), 'That doesn’t look like an e-mail address.')
  assert.equal(emailError(' ana@buybusinessclass.com '), '')
  assert.equal(emailOk('a.b+c@d.co'), true)
  assert.equal(emailOk('ana@buybusinessclass'), false)
})

test('the one password rule is the server’s: 8 to 128 characters', () => {
  assert.deepEqual(passwordRules(''), [{ ok: false, text: 'At least 8 characters' }])
  assert.deepEqual(passwordRules('1234567'), [{ ok: false, text: 'At least 8 characters' }])
  assert.deepEqual(passwordRules('12345678'), [{ ok: true, text: 'At least 8 characters' }])
  assert.deepEqual(passwordRules('x'.repeat(128)), [{ ok: true, text: 'At least 8 characters' }])
  assert.deepEqual(passwordRules('x'.repeat(129)), [{ ok: false, text: 'At most 128 characters' }])
})

test('an invite link that failed says why, and what to do next', () => {
  // set-password answers 400 "<reason>: <text>".
  assert.equal(linkProblem('expired: This invite link has expired. Request a new one below.'), 'expired')
  assert.equal(linkProblem('used: This invite link was already used.'), 'used')
  assert.equal(linkProblem('superseded: A newer invite was sent'), 'superseded')
  assert.equal(linkProblem('unknown: This invite link isn’t valid.'), 'unknown')
  assert.equal(linkProblem('Invite token is malformed'), null)
  assert.equal(linkProblem(''), null)
  assert.equal(LINK_TEXT.used.action, 'sign_in')
  for (const k of ['expired', 'superseded', 'unknown'] as const) assert.equal(LINK_TEXT[k].action, 'new_link')
  assert.match(LINK_TEXT.expired.text, /48 hours/)
})

test('the CRM sign-in: who the CRM thinks you are, and why it failed', () => {
  const payload = btoa(JSON.stringify({ email: 'ana@buybusinessclass.com', exp: 1 })).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  assert.equal(crmEmail(`h.${payload}.sig`), 'ana@buybusinessclass.com')
  assert.equal(crmEmail('not-a-token'), null)
  assert.equal(crmEmail(`h.${btoa('{"sub":"x"}')}.s`), null)
  assert.equal(crmEmail(null), null)
  // A 401 has several reasons; the server's detail decides which.
  assert.equal(crmProblem(401, 'No matching BBC account for this user').kind, 'no_account')
  assert.equal(crmProblem(401, 'SSO token expired — please reload from the CRM').kind, 'expired')
  assert.equal(crmProblem(401, 'Invalid SSO token').kind, 'invalid')
  assert.equal(crmProblem(401, 'SSO token is not valid for login').kind, 'invalid')
  assert.equal(crmProblem(401, null).kind, 'invalid')
  assert.equal(crmProblem(403, 'Account disabled — contact admin').kind, 'off')
  assert.equal(crmProblem(400, "SSO token is missing the 'email' claim.").kind, 'no_email')
  assert.equal(crmProblem(503, 'CRM SSO not configured').kind, 'not_set_up')
  assert.equal(crmProblem(null, null).kind, 'unknown')
  assert.match(crmProblem(401, 'No matching BBC account for this user').head, /isn’t in the chat panel/)
  assert.match(crmProblem(401, 'SSO token expired — please reload from the CRM').head, /expired/)
})

test('after signing in you go back where you were, only on this site', () => {
  const o = 'https://admin.example.com'
  // Search and hash kept: /chats?highlight=… reopens the chat.
  assert.equal(backAfterSignIn('/chats?highlight=c1#m2', o), '/chats?highlight=c1#m2')
  assert.equal(backAfterSignIn('/leads', o), '/leads')
  assert.equal(backAfterSignIn(`${o}/tasks?task=t1`, o), '/tasks?task=t1')
  // An encoded slash stays a path on this site.
  assert.equal(backAfterSignIn('/%2F%2Fevil.com', o), '/%2F%2Fevil.com')
  // Anything that resolves to another site goes home instead.
  for (const bad of ['//evil.com/x', '/\\evil.com', '/\t/evil.com', 'https://evil.com/', 'http://admin.example.com/', 'javascript:alert(1)',
    // Same origin, but the path left would read as another site ("//evil.com").
    `${o}//evil.com/x`, '/.//evil.com', '/a/..//evil.com']) {
    assert.equal(backAfterSignIn(bad, o), null, bad)
  }
  assert.equal(backAfterSignIn(undefined, o), null)
  assert.equal(backAfterSignIn('', o), null)
})
