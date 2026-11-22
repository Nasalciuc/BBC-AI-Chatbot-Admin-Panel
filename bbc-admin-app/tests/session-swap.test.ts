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
import { cacheBelongsToAnother, cookieSessionChange, jwtClaims, userFromClaims } from '../src/lib/session-swap.ts'

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
  assert.match(fn, /if \(change === 'token'\) \{\s*const claims = jwtClaims\(fromCookie\)\s*useAuthStore\.setState/)
})

// CodeRabbit #244: a photo saved in another tab came with a newer token, but
// this tab kept the user it had loaded, so the menu showed the old photo.
test('a newer token of the same person brings their new photo, name and phone', () => {
  const store = readFileSync(join(root, 'src/stores/auth-store.ts'), 'utf-8')
  const fn = store.slice(store.indexOf('export function syncAccessTokenFromCookie(): void {'), store.indexOf('export function hasSession'))
  assert.match(fn, /accessToken: fromCookie,\s*user: state\.auth\.user && claims \? userFromClaims\(claims\) : state\.auth\.user,/)
  const claims = { sub: 'u-ana', email: 'ana@buybusinessclass.com', name: 'Ana Ceban', role: 'owner', tunnel_scope: 'all', exp: 9e9 }
  const u = userFromClaims({ ...claims, avatar_url: 'https://example.com/ana.jpg', phone: '+373 69 000 000' })
  assert.deepEqual(u, {
    accountNo: 'u-ana', email: 'ana@buybusinessclass.com', name: 'Ana Ceban', role: 'owner', tunnelScope: 'all',
    exp: 9e12, avatar_url: 'https://example.com/ana.jpg', phone: '+373 69 000 000',
  })
  // A removed photo is null (initials), not the old one.
  assert.equal(userFromClaims({ ...claims, avatar_url: null }).avatar_url, null)
  assert.equal(userFromClaims({ sub: 'u-cy' }).role, 'sales') // no role reads as sales, like cookieSessionChange
  // The sign-in guard reads a token the same way.
  const guard = readFileSync(join(root, 'src/routes/_authenticated/route.tsx'), 'utf-8')
  assert.match(guard, /auth\.setUser\(userFromClaims\(claims\)\)/)
})

test('the query cache is one account\'s: emptied as someone signs in, never at a renewal (CodeRabbit #273)', () => {
  const bob = { accountNo: 'u-bob' }
  // A sign-in after a session that ended, or after none; the CRM signing in someone else on this page.
  assert.equal(cacheBelongsToAnother(null, ana), true)
  assert.equal(cacheBelongsToAnother(undefined, ana), true)
  assert.equal(cacheBelongsToAnother(ana, bob), true)
  // A newer token or a saved profile of the same person, and the session ending (its next sign-in empties it).
  assert.equal(cacheBelongsToAnother(ana, { ...ana }), false)
  assert.equal(cacheBelongsToAnother(ana, null), false)
  assert.equal(cacheBelongsToAnother(null, null), false)
  // Wired where the cache lives: the auth store's every change, before anything of the new account loads.
  const main = readFileSync(join(root, 'src/main.tsx'), 'utf8')
  // Reset, so a page on screen drops what it shows and asks again as the new account (CodeRabbit #273).
  assert.match(main, /useAuthStore\.subscribe\(\(state, prev\) => \{\s+if \(cacheBelongsToAnother\(prev\.auth\.user, state\.auth\.user\)\) void queryClient\.resetQueries\(\)\s+\}\)/)
})

test('every sign-in sets the new token before the user: the reset asks again with the new session (CodeRabbit #273)', () => {
  // The password (and MFA) sign-in, the CRM's sign-in and a saved profile: the user change resets the cache and
  // refetches what is on screen at once, and authHeaders() reads the cookie that setAccessToken writes.
  const places: Array<[string, string, string]> = [
    ['src/features/auth/sign-in/index.tsx', 'function finishLogin(', 'auth.setAccessToken(token)'],
    ['src/lib/crm-embed-auth.ts', 'function applyToken(', 'auth.setAccessToken(token)'],
    ['src/features/settings/profile/index.tsx', 'auth.setAccessToken(res.token)', 'auth.setAccessToken(res.token)'],
  ]
  for (const [file, from, token] of places) {
    const src = readFileSync(join(root, file), 'utf8')
    const start = src.indexOf(from)
    assert.ok(start >= 0, `${file}: ${from} not found`)
    const t = src.indexOf(token, start)
    const u = src.indexOf('auth.setUser(', start)
    assert.ok(t >= 0 && u > t, `${file}: the token must be set before the user`)
  }
})
