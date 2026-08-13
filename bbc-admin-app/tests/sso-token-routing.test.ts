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
