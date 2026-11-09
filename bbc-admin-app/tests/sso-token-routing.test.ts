/**
 * SSO guard must not eat invite tokens.
 *
 * The global CRM-SSO bootstrap consumed ANY ?token= on load — POSTed it to
 * crm-exchange (401 on invite tokens), then stripped the query, so
 * /set-password mounted tokenless → "Invalid Link" for every invited
 * operator. Pins: the routing rules (pure), and the source contracts that
 * the bootstrap uses them and only strips the URL on SUCCESS.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

import { isPublicTokenRoute, pickSsoToken } from '../src/lib/sso-token-routing.ts'
import { afterCrmSignIn, holdSignInUntil } from '../src/lib/sign-in-gate.ts'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p: string) => readFileSync(join(root, p), 'utf-8')

test('invite link: /set-password?token=x → NO exchange, token left for the page', () => {
  assert.equal(pickSsoToken('/set-password', '?token=inv-123'), null)
  assert.equal(pickSsoToken('/set-password/', '?token=inv-123'), null)
})

test('CRM embed path still exchanges the legacy ?token=', () => {
  assert.deepEqual(pickSsoToken('/', '?token=crm-jwt'), ['token', 'crm-jwt'])
  assert.deepEqual(pickSsoToken('/chats', '?token=crm-jwt'), ['token', 'crm-jwt'])
})

test('?sso_token= is the SSO-owned name and wins everywhere', () => {
  assert.deepEqual(pickSsoToken('/', '?sso_token=crm-jwt'), ['sso_token', 'crm-jwt'])
  // Even on a public-token route: sso_token is explicitly SSO's.
  assert.deepEqual(
    pickSsoToken('/set-password', '?sso_token=crm-jwt&token=inv-123'),
    ['sso_token', 'crm-jwt'],
  )
})

test('no token at all → null', () => {
  assert.equal(pickSsoToken('/', ''), null)
  assert.equal(pickSsoToken('/', '?highlight=abc'), null)
})

test('public-token route matching is prefix-safe, not substring', () => {
  assert.ok(isPublicTokenRoute('/set-password'))
  assert.ok(isPublicTokenRoute('/set-password/step2'))
  assert.ok(!isPublicTokenRoute('/set-password-help'))
  assert.ok(!isPublicTokenRoute('/'))
})

test('bootstrap uses the routing rules and strips ONLY on success', () => {
  const src = read('src/lib/crm-embed-auth.ts')
  assert.match(src, /pickSsoToken\(window\.location\.pathname/)
  // Strip happens exactly once, gated on the applied-token success.
  assert.match(src, /if \(ok\) stripTokenFromUrl\(param\)/)
  const stripCalls = src.match(/stripTokenFromUrl\(param\)/g) ?? []
  assert.equal(stripCalls.length, 1, 'failure paths must leave the URL intact')
})

// ── The sign-in guard waits for the CRM's sign-in ────────────────────────
// The CRM opens the panel with its token, and on a browser shared between
// shifts the cookie can still hold the previous agent's session. Nothing may
// load from it before the CRM's sign-in has settled: no chat list, no
// heartbeat, no request with the old token.

test('no CRM sign-in running: the guard runs at once, as before', () => {
  let ran = false
  const out = afterCrmSignIn(() => {
    ran = true
    return 'checked'
  })
  assert.equal(ran, true)
  assert.equal(out, 'checked')
})

test('a CRM sign-in running: the guard runs only once it settles, worked or failed', async () => {
  for (const outcome of ['worked', 'failed'] as const) {
    let settle!: (ok: boolean) => void
    let fail!: (e: unknown) => void
    const signIn = new Promise<boolean>((res, rej) => {
      settle = res
      fail = rej
    })
    holdSignInUntil(signIn)
    const order: string[] = []
    const guarded = afterCrmSignIn(() => {
      order.push('guard')
      return 'checked'
    })
    assert.ok(guarded instanceof Promise, 'held while the sign-in runs')
    await new Promise((r) => setTimeout(r, 5))
    assert.deepEqual(order, [], `${outcome}: nothing ran before the sign-in settled`)
    if (outcome === 'worked') settle(true)
    else fail(new Error('exchange failed'))
    assert.equal(await guarded, 'checked')
    assert.deepEqual(order, ['guard'])
    // Settled: the next navigation is checked at once again.
    assert.equal(afterCrmSignIn(() => 'now'), 'now')
  }
})

test('the guard\'s redirect to sign-in still reaches the router after the wait', async () => {
  holdSignInUntil(Promise.resolve(false))
  const toSignIn = { to: '/sign-in' }
  await assert.rejects(
    Promise.resolve(
      afterCrmSignIn(() => {
        throw toSignIn
      }),
    ),
    (e) => e === toSignIn,
  )
})

test('held only when the panel was opened with a CRM token, and the guard waits through the gate', () => {
  const auth = read('src/lib/crm-embed-auth.ts')
  const install = auth.slice(auth.indexOf('export function installCrmEmbedAuth'))
  assert.match(install, /fromCrm = pickSsoToken\(window\.location\.pathname, window\.location\.search\) !== null/)
  assert.match(install, /const exchange = tryCrmUrlTokenLogin\(\)\s*if \(fromCrm\) holdSignInUntil\(exchange\)/)

  const guard = read('src/routes/_authenticated/route.tsx')
  assert.match(guard, /beforeLoad: \(\{ location \}\) => afterCrmSignIn\(\(\) => requireSession\(location\.href\)\)/)
})
