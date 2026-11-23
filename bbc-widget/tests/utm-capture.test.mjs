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
import { captureUtm, getUtm, namesWhereFrom, sessionSource, sourceToAdd } from '../src/utm.ts'

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

// ── a chat whose tab kept nothing: the visit's PostHog session says where it came from (point D, 23 Nov) ──────────
// On 14-22 Nov 33 chats reached the server with no source at all, over half from Apple's browsers; PostHog knew 29.

const HOST = 'buybusinessclass.com'

test('the PostHog session names the source the tab lost: its first page\'s tags and click ids, and the site before it', () => {
  const entry = {
    // Its first page's address: only the known keys are read from it, never the rest.
    $session_entry_url: 'https://buybusinessclass.com/flights/rome?utm_source=fb&utm_medium=paid&fbclid=f-9&email=someone%40example.com',
    $session_entry_referrer: 'https://l.facebook.com/',
    $session_entry_referring_domain: 'l.facebook.com',
    $session_entry_utm_source: 'fb',
    $session_entry_fbclid: 'f-9',
    $session_entry_pathname: '/flights/rome',
  }
  assert.deepEqual(sessionSource(entry, HOST), {
    utm_source: 'fb', utm_medium: 'paid', fbclid: 'f-9', referrer: 'https://l.facebook.com/', source_via: 'posthog',
  })
  // A click id PostHog does not keep on its own (Kayak's) is read from the first page's address.
  assert.deepEqual(sessionSource({ $session_entry_url: 'https://buybusinessclass.com/?kayak_click_id=k-1', $session_entry_referrer: '$direct' }, HOST),
    { kayak_click_id: 'k-1', source_via: 'posthog' })
})

test('PostHog saw no site before the visit: direct, and nothing else', () => {
  assert.deepEqual(sessionSource({ $session_entry_url: 'https://www.buybusinessclass.com/', $session_entry_referrer: '$direct' }, HOST),
    { source_via: 'direct' })
})

test('a session that began on the site itself, or an entry that is not PostHog\'s, says nothing', () => {
  assert.deepEqual(sessionSource({ $session_entry_url: 'https://buybusinessclass.com/deals', $session_entry_referrer: 'https://www.buybusinessclass.com/' }, HOST), {})
  for (const bad of [null, undefined, 'fb', 42, [], { $session_entry_url: 'not an address' }]) {
    assert.deepEqual(sessionSource(bad, HOST), {}, String(bad))
  }
  // A referrer that is no web address is no site.
  assert.deepEqual(sessionSource({ $session_entry_referrer: 'android-app://com.google.android.gm/' }, HOST), {})
})

test('the session\'s word is added only when the tab names nothing, and only where the site lets the chat keep it', () => {
  open(HOME)
  captureUtm()
  const entry = { $session_entry_referrer: 'https://l.facebook.com/', $session_entry_utm_source: 'fb' }
  // The tab kept only the page: the session says the rest.
  assert.deepEqual(sourceToAdd(getUtm(), entry, HOST), { utm_source: 'fb', referrer: 'https://l.facebook.com/', source_via: 'posthog' })
  // A page of the site itself before this one is no source either.
  assert.equal(namesWhereFrom({ page_url: HOME, referrer: 'https://buybusinessclass.com/deals' }, HOST), false)
  assert.deepEqual(sourceToAdd({ page_url: HOME, referrer: 'https://buybusinessclass.com/deals' }, entry, HOST).source_via, 'posthog')
  // The tab's own record wins whenever it names where the visit came from: a tag, a click id, another site.
  open(AD, 'https://www.bing.com/')
  captureUtm()
  assert.deepEqual(sourceToAdd(getUtm(), entry, HOST), {})
  assert.deepEqual(sourceToAdd({ page_url: HOME, gclid: 'g-1' }, entry, HOST), {})
  assert.deepEqual(sourceToAdd({ page_url: HOME, referrer: 'https://www.google.com/' }, entry, HOST), {})
  // Without marketing consent (data-attribution="off") nothing is added, whatever PostHog says.
  captureUtm({ attribution: false })
  assert.deepEqual(sourceToAdd(getUtm(), entry, HOST), {})
})
