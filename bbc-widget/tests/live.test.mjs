// "On the site now" in the widget (src/live.ts, contracts/live.md): what a beat carries (never what the visitor
// typed), how a hello is read (the contract's shape, plain text, not past its time) and the card that shows it.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { beatBody, nextPollDelay, readConfig, readHello, sourceOf } from '../src/liveRead.ts'

const here = dirname(fileURLToPath(import.meta.url))
const contract = JSON.parse(readFileSync(join(here, '..', '..', 'contracts', 'live_inbox_message.json'), 'utf8'))
const NOW = Date.parse('2026-11-21T22:05:00+00:00')

test('the message the API writes is read as a hello', () => {
  const h = readHello({ ok: true, messages: [contract] }, NOW)
  assert.deepEqual(h, {
    id: contract.id, agent: 'Ana', text: contract.text, tap: contract.tap, expiresAt: Date.parse(contract.expires_at),
  })
})

test('a hello past its time, of another kind, too long, or without a tap is no hello', () => {
  const bad = [
    { ...contract, expires_at: '2026-11-21T22:04:59+00:00' },
    { ...contract, kind: 'ad' },
    { ...contract, text: 'x'.repeat(301) },
    { ...contract, text: '   ' },
    { ...contract, tap: 'v1.not-a-tap' },
    { ...contract, id: '' },
    'a string',
    null,
  ]
  for (const m of bad) assert.equal(readHello({ messages: [m] }, NOW), null, JSON.stringify(m))
  assert.equal(readHello({ messages: 'nope' }, NOW), null)
  assert.equal(readHello(null, NOW), null)
})

test('the first good hello wins, and a long name is cut', () => {
  const h = readHello({ messages: [{ ...contract, kind: 'x' }, { ...contract, id: 'h2', agent: 'A'.repeat(80) }] }, NOW)
  assert.equal(h.id, 'h2')
  assert.equal(h.agent.length, 40)
})

test('the config: on only with a URL, numbers kept in range', () => {
  assert.deepEqual(readConfig({ enabled: false, url: 'https://live.example.test' }), { enabled: false })
  assert.deepEqual(readConfig({ enabled: true }), { enabled: false })
  assert.deepEqual(readConfig({ enabled: true, url: 'javascript:alert(1)' }), { enabled: false })
  assert.deepEqual(readConfig({ enabled: true, url: 'https://live.example.test/', after_s: 20, beat_s: 9999, hello: true }),
    { enabled: true, url: 'https://live.example.test', after_s: 20, beat_s: 15, hello: true })
  assert.deepEqual(readConfig(null), { enabled: false })
})

test('a beat carries the path, codes and dates, never the query', () => {
  const b = beatBody({
    path: '/business-class-flights?from=JFK&email=dana@example.com#x',
    trip: { source: 'kayak', origin_code: 'jfk', destination_code: 'IST', origin_name: 'New York', depart_date: '2026-12-14',
      return_date: '14 Dec', cabin: 'business', adults: 2, children: 1 },
    utm: { utm_source: 'kayak', utm_medium: 'cpc' }, phone: true, site: 'bbc', chatOpen: false, hasChat: false,
  })
  assert.deepEqual(b, {
    page: '/business-class-flights', from: 'JFK', to: 'IST', depart: '2026-12-14', cabin: 'business', pax: 3,
    source: 'kayak', device: 'phone', site: 'bbc', chat_open: false, has_chat: false,
  })
  assert.doesNotMatch(JSON.stringify(b), /dana|New York|14 Dec/)
})

test('a page without a trip beats the path alone', () => {
  const b = beatBody({ path: '', trip: null, utm: {}, phone: false, site: 'bct', chatOpen: true, hasChat: false })
  assert.deepEqual(b, { page: '/', source: 'site', device: 'computer', site: 'bct', chat_open: true, has_chat: false })
})

test('an ad, as the moments read it (triggers.adVisit), is an ad here too', async () => {
  const { adVisit } = await import('../src/triggers.ts')
  const samples = [{ gclid: 'g' }, { msclkid: 'm' }, { utm_source: 'google', utm_medium: 'cpc' },
    { utm_source: 'bing', utm_medium: 'paid_search' }, { utm_source: 'kayak', utm_medium: 'cpc', gclid: 'g' }]
  for (const s of samples) {
    if (adVisit(s)) assert.equal(sourceOf(s), 'ads', JSON.stringify(s))
  }
})

test('where they came from, as the panel reads it', () => {
  assert.equal(sourceOf({ utm_source: 'Kayak', utm_medium: 'cpc' }), 'kayak')
  assert.equal(sourceOf({ kayak_click_id: 'k1' }), 'kayak')
  assert.equal(sourceOf({ gclid: 'g1' }), 'ads')
  assert.equal(sourceOf({ utm_source: 'bing', utm_medium: 'cpc' }), 'ads')
  assert.equal(sourceOf({ fbclid: 'f1' }), 'ads')
  assert.equal(sourceOf({ utm_source: 'facebook', utm_medium: 'paid_social' }), 'ads')
  assert.equal(sourceOf({ utm_source: 'newsletter', utm_medium: 'email' }), 'site')
  assert.equal(sourceOf({}), 'site')
})

test('the poll waits again at once after an answer, longer after failures', () => {
  assert.equal(nextPollDelay(204, 0), 0)
  assert.equal(nextPollDelay(200, 0), 0)
  assert.equal(nextPollDelay('error', 1), 4_000)
  assert.equal(nextPollDelay(502, 9), 30_000)
})

test('the card shows the hello as text, never as markup', () => {
  const src = readFileSync(join(here, '..', 'src', 'FloatingButtons.tsx'), 'utf8')
  const card = src.slice(src.indexOf('bw-nudge--hello'), src.indexOf(') : card && count === 0'))
  assert.match(card, /\{hello\.text\}/)
  assert.doesNotMatch(card, /dangerouslySetInnerHTML|innerHTML/)
})
