/**
 * The lead must say where the visitor came from whenever the address bar said it.
 *
 * Two ways it was lost:
 *  - the tags lived only in the tab's storage. Between 10 Sep and 4 Nov 2026, 90 of 725 chats from Apple's browsers
 *    reached the server with no page, no referrer and no tags at all (0 of 596 everywhere else): the storage gave
 *    nothing back when the form was sent. What the page load saw is now kept in memory as well;
 *  - "first touch" meant the first PAGE of the tab, tags or not: a visitor who typed the address and then came
 *    back through an ad in the same tab stayed "direct". The first page that names a source now wins over a
 *    first page that named none; a page without tags never replaces one with tags.
 *
 * Plain .mjs + strip-types import: the widget's zero-dep test pattern.
 */
import { test, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { captureUtm, getUtm } from '../src/utm.ts'

function fakeStorage() {
  const m = new Map()
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
    clear: () => m.clear(),
  }
}

/** Safari with "Block All Cookies": every touch of the storage throws. */
function blockedStorage() {
  const refuse = () => { throw new Error('SecurityError: The operation is insecure.') }
  return { getItem: refuse, setItem: refuse, removeItem: refuse, clear: refuse }
}

const AD = 'https://buybusinessclass.com/business-class-flights/chicago-to-london?utm_source=bing&utm_medium=cpc&utm_campaign=lon&msclkid=m-1'
const HOME = 'https://buybusinessclass.com/'
const OTHER_AD = 'https://buybusinessclass.com/?utm_source=fb&utm_medium=paid&fbclid=f-2'

/** A page of the same tab: the storage stays, the address and the referrer change. */
function open(url, referrer = '') {
  globalThis.window = { location: new URL(url) }
  globalThis.document = { referrer, cookie: '' }
}

beforeEach(() => {
  globalThis.sessionStorage = fakeStorage()
  open(HOME)
  captureUtm({ attribution: false })      // forget what an earlier test's page load saw
})

// ── the storage is not the only place ───────────────────────────────────

test('a blocked storage does not cost the lead its source', () => {
  globalThis.sessionStorage = blockedStorage()
  open(AD, 'https://www.bing.com/')
  assert.doesNotThrow(() => captureUtm())
  const u = getUtm()
  assert.equal(u.utm_source, 'bing')
  assert.equal(u.utm_medium, 'cpc')
  assert.equal(u.utm_campaign, 'lon')
  assert.equal(u.msclkid, 'm-1')
  assert.equal(u.referrer, 'https://www.bing.com/')
  assert.equal(u.page_url, 'https://buybusinessclass.com/business-class-flights/chicago-to-london')
})

test('a storage emptied after the capture does not either', () => {
  open(AD)
  captureUtm()
  sessionStorage.clear()                   // the page, or the browser, emptied it
  assert.equal(getUtm().utm_source, 'bing')
  assert.equal(getUtm().msclkid, 'm-1')
})

test('a blocked storage and a page without tags: the lead still names its page and its referrer', () => {
  globalThis.sessionStorage = blockedStorage()
  open('https://buybusinessclass.com/search?from=ORD&to=LHR', 'https://www.google.com/')
  captureUtm()
  assert.deepEqual(getUtm(), { page_url: 'https://buybusinessclass.com/search', referrer: 'https://www.google.com/' })
})

test('a value in the storage that is not ours is replaced, not trusted', () => {
  sessionStorage.setItem('bbc_utm', 'not json')
  open(AD)
  captureUtm()
  assert.equal(getUtm().utm_source, 'bing')
  assert.equal(JSON.parse(sessionStorage.getItem('bbc_utm')).utm_source, 'bing')
})

// ── first touch is the first page that names a source ───────────────────

test('typed the address, then came through an ad in the same tab: the ad is where they came from', () => {
  open(HOME)
  captureUtm()
  assert.equal(getUtm().utm_source, undefined)
  open(AD, 'https://www.bing.com/')
  captureUtm()
  const u = getUtm()
  assert.equal(u.utm_source, 'bing')
  assert.equal(u.msclkid, 'm-1')
  assert.equal(u.page_url, 'https://buybusinessclass.com/business-class-flights/chicago-to-london')
})

test('a click id alone names a source too', () => {
  open(HOME)
  captureUtm()
  open('https://buybusinessclass.com/?gclid=g-9')
  captureUtm()
  assert.equal(getUtm().gclid, 'g-9')
})

test('the first source stays when a later page names another (unchanged)', () => {
  open(AD)
  captureUtm()
  open(OTHER_AD)
  captureUtm()
  const u = getUtm()
  assert.equal(u.utm_source, 'bing')
  assert.equal(u.fbclid, undefined)
})

test('a later page without tags never replaces the source (unchanged)', () => {
  open(AD)
  captureUtm()
  open('https://buybusinessclass.com/search?from=ORD&to=LHR', AD)
  captureUtm()
  const u = getUtm()
  assert.equal(u.utm_source, 'bing')
  assert.equal(u.page_url, 'https://buybusinessclass.com/business-class-flights/chicago-to-london')
})

test('with the storage blocked, each page stands alone: the page that names the source carries it', () => {
  globalThis.sessionStorage = blockedStorage()
  open(HOME)
  captureUtm()
  open(AD)
  captureUtm()
  assert.equal(getUtm().utm_source, 'bing')
})

// ── without marketing consent nothing is kept, in memory either ─────────

test('attribution off forgets what this page load saw, not only the storage', () => {
  open(AD)
  captureUtm()
  assert.equal(getUtm().utm_source, 'bing')
  captureUtm({ attribution: false })
  assert.deepEqual(getUtm(), { page_url: 'https://buybusinessclass.com/business-class-flights/chicago-to-london' })
})

test('attribution off with a blocked storage: still only the page', () => {
  globalThis.sessionStorage = blockedStorage()
  open(AD)
  assert.doesNotThrow(() => captureUtm({ attribution: false }))
  assert.deepEqual(getUtm(), { page_url: 'https://buybusinessclass.com/business-class-flights/chicago-to-london' })
})
