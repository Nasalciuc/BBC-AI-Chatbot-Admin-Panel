// The visit registry in the widget (src/visits.ts, src/visitsRead.ts; contracts/live.md "The visit registry"): what a
// page's snapshot carries (never what the visitor typed), the events as short words, the card's answer that only
// moves forward, where the visitor came from, and the config that turns it on.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  MAX_EVENTS, VISIT_EVENTS, addEvent, cardAfter, channelOf, eventToken, newPageId, readVisitCard, visitBody,
} from '../src/visitsRead.ts'
import { readConfig } from '../src/liveRead.ts'

const here = dirname(fileURLToPath(import.meta.url))
const NOW = Date.parse('2026-11-25T12:00:00+00:00')
const tokens = JSON.parse(readFileSync(join(here, '..', '..', 'contracts', 'live_tokens.json'), 'utf8'))
const PASS = tokens.pass.valid[0].value

// bbc-live's own shapes (internal/live/registry.go): what the widget sends must pass them.
const EVENT_SHAPE = /^[a-z][a-z_]{0,31}(:[a-z0-9_]{1,24}){0,2}(\*[1-9][0-9]{0,2})?$/

test('an event is a short word with the widget’s own words after it, never a value someone typed', () => {
  assert.equal(eventToken('proactive_shown', { trigger: 'kayak', surface: 'card', variant: 'beat' }), 'card:kayak:card')
  assert.equal(eventToken('proactive_engaged', { reply: 'yes', seconds_to_reply: 4 }), 'card_yes:yes')
  assert.equal(eventToken('chat_channel_given', { channel: 'sms', tunnel: 'sales' }), 'contact:sms')
  assert.equal(eventToken('chat_message_sent', { text: 'call me at 415 555 0123' }), 'chat_msg')
  assert.equal(eventToken('proactive_eligible', { trigger: 'ads', arm: 'agent_first' }), 'moment:ads:agent_first')
  // A qualifier that isn't one of the words ends the token there: no address, number or sentence passes.
  assert.equal(eventToken('chat_channel_given', { channel: 'dana@example.com' }), 'contact')
  assert.equal(eventToken('proactive_shown', { trigger: 'Kayak Fares!', surface: 'card' }), 'card')
  assert.equal(eventToken('call_initiated', { entry_point: '+1 415 555 0123' }), 'call')
  // An event the registry doesn't keep is nothing (the message that takes no branch).
  assert.equal(eventToken('chat_message_text', { text: 'hello' }), null)
  assert.equal(eventToken('$pageview', {}), null)
  for (const [name, [word]] of Object.entries(VISIT_EVENTS)) {
    assert.match(eventToken(name, {}), EVENT_SHAPE, name)
    assert.match(word, /^[a-z][a-z_]{0,31}$/)
  }
})

test('the same event again counts, and a page keeps at most as many as bbc-live', () => {
  let list = []
  for (const t of ['card:kayak:card', 'chat_msg', 'chat_msg', 'chat_msg', 'contact:sms', 'chat_msg']) list = addEvent(list, t)
  assert.deepEqual(list, ['card:kayak:card', 'chat_msg*3', 'contact:sms', 'chat_msg'])
  for (const t of list) assert.match(t, EVENT_SHAPE)
  let many = []
  for (let i = 0; i < MAX_EVENTS + 10; i++) many = addEvent(many, i % 2 ? 'chat_reply' : 'chat_msg')
  assert.equal(many.length, MAX_EVENTS)
  let same = ['chat_msg*999']
  same = addEvent(same, 'chat_msg')
  assert.deepEqual(same, ['chat_msg*999'], 'the count stops where bbc-live’s shape does')
})

test('what came of the card only moves forward, and a second card is not the card', () => {
  let card = cardAfter(null, 'proactive_dismissed', { how: 'not_now' }, NOW)
  assert.equal(card, null, 'nothing to answer before a card shows')
  card = cardAfter(card, 'proactive_shown', { trigger: 'kayak', surface: 'card', variant: 'beat' }, NOW)
  assert.deepEqual(card, { trigger: 'kayak', variant: 'beat', surface: 'card', state: 'shown', at: NOW })
  const note = cardAfter(card, 'proactive_shown', { trigger: 'note', surface: 'panel' }, NOW + 1000)
  assert.equal(note, card, 'the first card of the visit stays the card')
  card = cardAfter(card, 'proactive_dismissed', { how: 'not_now' }, NOW + 5000)
  assert.equal(card.state, 'dismissed')
  assert.equal(card.answer, 'not_now')
  card = cardAfter(card, 'proactive_engaged', { reply: 'channel' }, NOW + 9000)
  assert.equal(card.state, 'engaged', 'the pill after "Not now" answered it')
  assert.equal(card.answeredAt, NOW + 9000)
  assert.equal(cardAfter(card, 'proactive_dismissed', { how: 'close' }, NOW + 20000), card, 'an answer stays an answer')
  assert.equal(cardAfter(null, 'proactive_shown', { trigger: 'popup', surface: 'card' }, NOW), null, 'an unknown moment')
  // Kept for the visit's next pages, read back in its shape only.
  assert.deepEqual(readVisitCard(JSON.stringify(card)), card)
  for (const bad of ['{', 'null', '{"state":"loved","trigger":"kayak","at":1}', '{"state":"shown","trigger":"kayak"}']) {
    assert.equal(readVisitCard(bad), null, bad)
  }
})

test('where the visitor came from, in one word', () => {
  const host = 'www.buybusinessclass.com'
  assert.equal(channelOf({ utm_source: 'kayak', utm_medium: 'cpc' }, '', host), 'kayak')
  assert.equal(channelOf({ kayak_click_id: 'k1' }, '', host), 'kayak')
  assert.equal(channelOf({ gclid: 'g1' }, 'https://www.google.com/', host), 'google')
  assert.equal(channelOf({ utm_source: 'google', utm_medium: 'cpc' }, '', host), 'google')
  assert.equal(channelOf({ fbclid: 'f1' }, '', host), 'meta')
  assert.equal(channelOf({ utm_source: 'instagram', utm_medium: 'paid_social' }, '', host), 'meta')
  assert.equal(channelOf({ msclkid: 'm1' }, '', host), 'bing')
  assert.equal(channelOf({ utm_source: 'newsletter', utm_medium: 'cpm' }, '', host), 'ads')
  assert.equal(channelOf({}, 'https://www.google.com/', host), 'search', 'Google without an ad: search')
  assert.equal(channelOf({}, 'https://duckduckgo.com/', host), 'search')
  assert.equal(channelOf({}, 'https://www.reddit.com/r/travel', host), 'referral')
  assert.equal(channelOf({}, 'https://buybusinessclass.com/deals', host), 'direct', 'our own site is no referral')
  assert.equal(channelOf({}, '', host), 'direct')
  assert.equal(channelOf({ utm_source: 'partner' }, '', host), 'other')
})

test('a snapshot carries the page, the trip as codes and dates, and nothing the visitor typed', () => {
  const card = { trigger: 'kayak', variant: 'beat', surface: 'card', state: 'engaged', at: NOW - 30_000, answer: 'yes',
    answeredAt: NOW - 20_000 }
  const b = visitBody({
    pageId: 'pabc123def456g', path: '/flights/new-york-istanbul?email=dana@example.com#top', pageType: 'route',
    pageMs: 61_500, seenMs: 40_200,
    trip: { origin_code: 'jfk', destination_code: 'IST', depart_date: '2026-12-14', return_date: '14 Dec', cabin: 'business', adults: 2 },
    utm: { utm_source: 'kayak', utm_medium: 'cpc', utm_campaign: 'dana@example.com', referrer: 'https://www.kayak.com/x' },
    referrer: 'https://www.kayak.com/x', siteHost: 'www.buybusinessclass.com', phone: true, site: 'bbc',
    lang: 'en-US', tz: 'America/New_York', country: 'US', phSession: '01937a2b-4c5d-7e6f-8a9b-0c1d2e3f4a5b',
    events: ['card:kayak:card', 'card_yes:yes'], steps: 'search:JFK-IST;quote_opened', card, hasChat: true,
    conversationId: '0b5e2c1a-7f3d-4e8a-9b6c-2d4f6a8c0e12', nowMs: NOW, pass: PASS,
  })
  const text = JSON.stringify(b)
  for (const leak of ['dana@example.com', '#top', '14 Dec', 'email=']) assert.ok(!text.includes(leak), leak)
  assert.deepEqual(b, {
    page_id: 'pabc123def456g', page: '/flights/new-york-istanbul', page_type: 'route', page_s: 61, seen_s: 40,
    source: 'kayak', channel: 'kayak', device: 'phone', site: 'bbc', has_chat: true,
    events: 'card:kayak:card;card_yes:yes', steps: 'search:JFK-IST;quote_opened',
    utm_source: 'kayak', utm_medium: 'cpc', referrer: 'www.kayak.com',
    from: 'JFK', to: 'IST', depart: '2026-12-14', cabin: 'business', pax: 2,
    lang: 'en-US', tz: 'America/New_York', country: 'US', ph_session: '01937a2b-4c5d-7e6f-8a9b-0c1d2e3f4a5b',
    conversation_id: '0b5e2c1a-7f3d-4e8a-9b6c-2d4f6a8c0e12',
    card: { trigger: 'kayak', variant: 'beat', surface: 'card', state: 'engaged', s: 30, answer: 'yes', answer_s: 20 },
    pass: PASS,
  })
  // No card, no chat, no consent for the source: the page and the screen still go.
  const plain = visitBody({ pageId: 'pabc123def456g', path: '', pageMs: 0, seenMs: 0, trip: null, utm: {}, referrer: '',
    siteHost: '', phone: false, site: 'bct', events: [], steps: '', card: null, hasChat: false, nowMs: NOW })
  assert.deepEqual(plain, { page_id: 'pabc123def456g', page: '/', page_s: 0, seen_s: 0, source: 'site', channel: 'direct',
    device: 'computer', site: 'bct', has_chat: false, events: '', steps: '' })
})

test('a page id is one bbc-live takes', () => {
  for (let i = 0; i < 50; i++) assert.match(newPageId(), /^[A-Za-z0-9_-]{6,32}$/)
  assert.notEqual(newPageId(), newPageId())
})

test('the API turns the registry on with the same place and pass, with or without the live list', () => {
  const only = readConfig({ enabled: false, url: 'https://live.example.test/', pass: PASS, visits: true, country: 'US' })
  assert.deepEqual(only, { enabled: false, url: 'https://live.example.test', pass: PASS, visits: true, country: 'US' })
  const both = readConfig({ enabled: true, url: 'https://live.example.test', pass: PASS, visits: true, after_s: 20 })
  assert.equal(both.enabled, true)
  assert.equal(both.visits, true)
  // Without a place, a pass or the word, no registry (the message that takes no branch): as before.
  assert.deepEqual(readConfig({ enabled: false, url: 'https://live.example.test', pass: PASS }), { enabled: false })
  assert.deepEqual(readConfig({ enabled: false, pass: PASS, visits: true }), { enabled: false })
  assert.deepEqual(readConfig({ enabled: false, url: 'https://live.example.test', visits: true }), { enabled: false })
  assert.equal(readConfig({ enabled: false, url: 'https://live.example.test', pass: PASS, visits: true, country: 'usa' }).country, undefined)
  assert.deepEqual(readConfig({ enabled: false, later: true, visits: true, url: 'https://live.example.test', pass: PASS }),
    { enabled: false, later: true }, 'later wins: no pass is used with it')
})

test('the registry starts at the page, never in the CRM’s chat, and its events come from the PostHog wrapper', () => {
  const entry = readFileSync(join(here, '..', 'src', 'entry.tsx'), 'utf8')
  assert.match(entry, /if \(!embedded\) startVisits\(\{ apiUrl, site: brand\.id \}\)/)
  const ph = readFileSync(join(here, '..', 'src', 'posthog.ts'), 'utf8')
  assert.match(ph, /tapCapture\(event, properties\)/)
  const visits = readFileSync(join(here, '..', 'src', 'visits.ts'), 'utf8')
  // The pass in the body, as plain text: no preflight, and sendBeacon for the page's last snapshot.
  assert.match(visits, /'Content-Type': 'text\/plain;charset=UTF-8'/)
  assert.match(visits, /navigator\.sendBeacon/)
  assert.ok(!/X-Live-Pass/.test(visits), 'no custom header on a snapshot')
  // The live list takes the same answer the registry asked for at the page's start: one ask a page.
  const live = readFileSync(join(here, '..', 'src', 'live.ts'), 'utf8')
  assert.match(live, /const start = \(\) => \{\n\s+if \(stopped\) return\n\s+pageConfig\(apiUrl\)/)
})
