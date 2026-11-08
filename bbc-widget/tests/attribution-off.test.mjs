/**
 * A site can load the chat for a visitor who has not accepted marketing cookies (buybusinessclass.com does when the
 * visitor taps the chat button: asking to chat is asking for the service). It says so on the script tag with
 * data-attribution="off", and then the widget keeps no ad click ids, campaign tags, referrer or Google Analytics id,
 * and drops any it kept earlier in the tab. The lead still says which page the chat started on (without the query).
 * Without the attribute (businessclass-tickets.com, the CRM) nothing changes.
 *
 * Plain .mjs + strip-types import: the widget's zero-dep test pattern.
 */
import { test, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { captureUtm, getUtm } from '../src/utm.ts'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')

function fakeStorage() {
  const m = new Map()
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
    _map: m,
  }
}

const LANDING = 'https://buybusinessclass.com/business-class-flights/chicago-to-london?utm_source=google&utm_campaign=lon&gclid=abc123'

beforeEach(() => {
  globalThis.sessionStorage = fakeStorage()
  globalThis.window = { location: new URL(LANDING) }
  globalThis.document = { referrer: 'https://www.google.com/', cookie: '_ga=GA1.1.111222333.1730000000; other=1' }
})

test('by default the widget keeps the campaign, the click id and the Analytics id (unchanged)', () => {
  captureUtm()
  const u = getUtm()
  assert.equal(u.utm_source, 'google')
  assert.equal(u.utm_campaign, 'lon')
  assert.equal(u.gclid, 'abc123')
  assert.equal(u.google_analytics_client_id, '111222333.1730000000')
  assert.equal(u.referrer, 'https://www.google.com/')
  assert.equal(u.page_url, 'https://buybusinessclass.com/business-class-flights/chicago-to-london')
})

test('attribution off: nothing is stored, and the lead carries only the page', () => {
  captureUtm({ attribution: false })
  assert.equal(sessionStorage.getItem('bbc_utm'), null)
  assert.deepEqual(getUtm(), { page_url: 'https://buybusinessclass.com/business-class-flights/chicago-to-london' })
})

test('attribution off drops what an earlier page of the tab kept', () => {
  sessionStorage.setItem('bbc_utm', JSON.stringify({ gclid: 'old', utm_source: 'bing', page_url: 'https://buybusinessclass.com/' }))
  captureUtm({ attribution: false })
  assert.equal(sessionStorage.getItem('bbc_utm'), null)
  assert.equal(getUtm().gclid, undefined)
})

test('the script tag decides before anything is captured', () => {
  const entry = readFileSync(join(SRC, 'entry.tsx'), 'utf8')
  const script = entry.indexOf('const script =')
  const capture = entry.indexOf('captureUtm(')
  assert.ok(script > -1, 'entry.tsx finds its script tag')
  assert.ok(capture > script, 'captureUtm runs after the script tag is found')
  assert.match(entry, /captureUtm\(\{\s*attribution:\s*script\?\.getAttribute\('data-attribution'\) !== 'off'\s*\}\)/)
})
