/**
 * Another tab signs in: what this tab does with the shared cookie.
 *
 * CodeRabbit #228 (raised twice): syncAccessTokenFromCookie took the new
 * token and kept the old user, so this tab showed one account's menus and
 * cached data while its requests went out as another account.
 * Runs with: node --experimental-strip-types --test tests/session-swap.test.ts
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { cookieSessionChange, jwtClaims } from '../src/lib/session-swap.ts'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')

function jwt(claims: Record<string, unknown>): string {
  const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString('base64url')
  return `${b64({ alg: 'HS256', typ: 'JWT' })}.${b64(claims)}.sig`
}

const ana = { accountNo: 'u-ana', role: 'owner' }
const anaToken = jwt({ sub: 'u-ana', role: 'owner', exp: 9e9 })

test('the same token, or no token, changes nothing', () => {
  assert.equal(cookieSessionChange(ana, anaToken, anaToken), 'none')
  assert.equal(cookieSessionChange(ana, anaToken, ''), 'none')
})

test('the same person in the same role: the newer token is taken', () => {
  assert.equal(cookieSessionChange(ana, anaToken, jwt({ sub: 'u-ana', role: 'owner', exp: 9e9 + 1 })), 'token')
  assert.equal(cookieSessionChange(null, '', anaToken), 'token') // no user loaded yet: the guard reads it
})

test('someone else, another role, or no readable token: the tab starts again', () => {
  assert.equal(cookieSessionChange(ana, anaToken, jwt({ sub: 'u-bob', role: 'sales' })), 'reload')
  assert.equal(cookieSessionChange(ana, anaToken, jwt({ sub: 'u-ana', role: 'sales' })), 'reload')
  assert.equal(cookieSessionChange({ accountNo: 'u-cy', role: 'sales' }, '', jwt({ sub: 'u-cy' })), 'token') // no role reads as sales, like the guard
  assert.equal(cookieSessionChange(ana, anaToken, 'not-a-jwt'), 'reload')
  assert.equal(jwtClaims('a.b.c'), null)
})

test('the store reloads on another session and takes the token on a renewal', () => {
  const store = readFileSync(join(root, 'src/stores/auth-store.ts'), 'utf-8')
  const fn = store.slice(store.indexOf('export function syncAccessTokenFromCookie(): void {'), store.indexOf('export function hasSession'))
  assert.match(fn, /const change = cookieSessionChange\(auth\.user, auth\.accessToken, fromCookie\)/)
  assert.match(fn, /if \(change === 'reload'\) \{\s*window\.location\.reload\(\)\s*return\s*\}/)
  assert.match(fn, /if \(change === 'token'\) \{\s*useAuthStore\.setState/)
})
