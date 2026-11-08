// The band at the top of the panel — pinned on lib/bands.ts.
// Runs with: node --experimental-strip-types --test tests/bands.test.ts

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { SESSION_WARN_MS, backTo, bandText, pickBand, secondsToNext, withoutSignInTokens, type BandInput } from '../src/lib/bands.ts'

const NOW = 1_000_000_000_000
const base: BandInput = { online: true, serverReachable: true, expMs: NOW + 3_600_000, nowMs: NOW, embedded: false, notLeader: false }

test('nothing wrong, no band; the most serious one wins', () => {
  assert.equal(pickBand(base), null)
  assert.equal(pickBand({ ...base, serverReachable: null }), null) // first check not back yet: no flash
  assert.equal(pickBand({ ...base, online: false, serverReachable: false, notLeader: true }), 'offline')
  assert.equal(pickBand({ ...base, serverReachable: false, notLeader: true, expMs: NOW + 60_000 }), 'server')
  assert.equal(pickBand({ ...base, expMs: NOW + 60_000, notLeader: true }), 'session')
  assert.equal(pickBand({ ...base, notLeader: true }), 'paused')
})

test('the session band: the last ten minutes, never in the CRM dock, gone once expired', () => {
  assert.equal(pickBand({ ...base, expMs: NOW + SESSION_WARN_MS }), 'session')
  assert.equal(pickBand({ ...base, expMs: NOW + SESSION_WARN_MS + 1 }), null)
  assert.equal(pickBand({ ...base, expMs: NOW + 60_000, embedded: true }), null)
  assert.equal(pickBand({ ...base, expMs: NOW - 1 }), null) // the next request's 401 takes over
  assert.equal(pickBand({ ...base, expMs: null }), null)
  assert.equal(bandText('session', { expMs: NOW + 9 * 60_000 + 1, nowMs: NOW }).head, 'Your session ends in 10 min.')
  assert.equal(bandText('session', { expMs: NOW + 20_000, nowMs: NOW }).head, 'Your session ends in 1 min.')
})

test('what each band says', () => {
  assert.equal(bandText('offline').head, 'You’re offline.')
  assert.equal(bandText('server', { retryInS: 7 }).text, 'Trying again in 7 s. What you type is kept.')
  assert.equal(bandText('server', { retryInS: 0 }).text, 'Trying again now. What you type is kept.')
  assert.match(bandText('paused').text, /active in the CRM, so chats ring there, not here/)
  assert.equal(secondsToNext(NOW, 30_000, NOW + 23_000), 7)
  assert.equal(secondsToNext(NOW, 30_000, NOW + 45_000), 0)
})

test('signing in again brings you back to the same chat', () => {
  assert.equal(backTo('/chats', '/chats', 'c4810'), '/chats?highlight=c4810')
  assert.equal(backTo('/chats', '/chats?highlight=old', 'c4810'), '/chats?highlight=c4810')
  assert.equal(backTo('/chats', '/chats', null), '/chats')
  assert.equal(backTo('/leads', '/leads?view=not_in_crm', 'c4810'), '/leads?view=not_in_crm')
})

test('a sign-in redirect never carries the CRM’s one-time token', () => {
  assert.equal(withoutSignInTokens('/chats?sso_token=abc.def.ghi'), '/chats')
  assert.equal(withoutSignInTokens('/chats?highlight=c1&token=xyz'), '/chats?highlight=c1')
  assert.equal(withoutSignInTokens('/leads?view=not_in_crm#top'), '/leads?view=not_in_crm#top')
  assert.equal(withoutSignInTokens('/'), '/')
})
