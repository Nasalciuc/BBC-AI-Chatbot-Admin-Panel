// The visit registry in the widget (src/visits.ts, src/visitsRead.ts; contracts/live.md "The visit registry"): what a
// page's snapshot carries (never what the visitor typed), the events as short words, the card's answer that only
// moves forward, where the visitor came from, and the config that turns it on.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  MAX_EVENTS, MAX_SEQ, VISIT_EVENTS, addEvent, afterSent, cardAfter, cardStillOn, cardToKeep, channelOf, eventToken,
  keptCardFor, newPageId, readSentMark, readVisitCard, snapshotTimer, visitBody,
} from '../src/visitsRead.ts'
import { configFor, readConfig } from '../src/liveRead.ts'

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
  card = cardAfter(card, 'proactive_dismissed', { trigger: 'kayak', how: 'not_now' }, NOW + 5000)
  assert.equal(card.state, 'dismissed')
  assert.equal(card.answer, 'not_now')
  card = cardAfter(card, 'proactive_engaged', { trigger: 'kayak', reply: 'channel' }, NOW + 9000)
  assert.equal(card.state, 'engaged', 'the pill after "Not now" answered it')
  assert.equal(card.answeredAt, NOW + 9000)
  assert.equal(cardAfter(card, 'proactive_dismissed', { trigger: 'kayak', how: 'close' }, NOW + 20000), card,
    'an answer stays an answer')
  assert.equal(cardAfter(null, 'proactive_shown', { trigger: 'popup', surface: 'card' }, NOW), null, 'an unknown moment')
  // Kept for the visit's next pages, read back in its shape only.
  assert.deepEqual(readVisitCard(JSON.stringify(card)), card)
  for (const bad of ['{', 'null', '{"state":"loved","trigger":"kayak","at":1}', '{"state":"shown","trigger":"kayak"}']) {
    assert.equal(readVisitCard(bad), null, bad)
  }
})

test('only the card’s own answer is what came of it: a note opened later and closed or answered is not (CodeRabbit #282, round 3)', () => {
  const kayak = cardAfter(null, 'proactive_shown', { trigger: 'kayak', surface: 'card', variant: 'beat' }, NOW)
  assert.equal(cardAfter(kayak, 'proactive_dismissed', { trigger: 'note', how: 'close' }, NOW + 5000), kayak)
  assert.equal(cardAfter(kayak, 'proactive_engaged', { trigger: 'note', reply: 'typed' }, NOW + 9000), kayak)
  const said = cardAfter(kayak, 'proactive_dismissed', { trigger: 'kayak', how: 'not_now' }, NOW + 5000)
  assert.equal(cardAfter(said, 'proactive_engaged', { trigger: 'note', reply: 'typed' }, NOW + 9000), said,
    '"Not now" to the card, then a reply to the note: the card stays "not now"')
  // The Meta ads' card is a card of the visit too: its answer counts and is kept for the visit's next pages.
  const social = cardAfter(null, 'proactive_shown', { trigger: 'social', surface: 'card' }, NOW)
  assert.equal(social?.trigger, 'social')
  const socialAnswered = cardAfter(social, 'proactive_engaged', { trigger: 'social', reply: 'yes', seconds_to_reply: 4 },
    NOW + 4000)
  assert.equal(socialAnswered.state, 'engaged')
  assert.deepEqual(readVisitCard(cardToKeep(socialAnswered)), socialAnswered, 'the answered card reads back whole')
})

test('a second opening of the card’s own moment is not the card: its seconds put it after the card (CodeRabbit #282, round 3)', () => {
  const first = cardAfter(null, 'proactive_shown', { trigger: 'note', surface: 'panel' }, NOW)
  // The first note was left unanswered; a second one showed a minute later and was answered or closed.
  assert.equal(cardAfter(first, 'proactive_engaged', { trigger: 'note', reply: 'typed', seconds_to_reply: 4 },
    NOW + 64_000), first)
  assert.equal(cardAfter(first, 'proactive_dismissed', { trigger: 'note', how: 'close', seconds_visible: 3 },
    NOW + 63_000), first)
  // The first note closed, the chat opened again and the second note answered: the card stays closed.
  const closed = cardAfter(first, 'proactive_dismissed', { trigger: 'note', how: 'close', seconds_visible: 6 }, NOW + 6_000)
  assert.equal(closed.state, 'dismissed')
  assert.equal(cardAfter(closed, 'proactive_engaged', { trigger: 'note', reply: 'typed', seconds_to_reply: 4 },
    NOW + 64_000), closed)
  // The card's own answer, its seconds rounded by the widget: counted.
  assert.equal(cardAfter(first, 'proactive_engaged', { trigger: 'note', reply: 'typed', seconds_to_reply: 12 },
    NOW + 12_400).state, 'engaged')
  // "Not now", the pill, then the reply on the same page: the reply counts from the card's own time, so it is the card's.
  const kayak = cardAfter(null, 'proactive_shown', { trigger: 'kayak', surface: 'card' }, NOW)
  const notNow = cardAfter(kayak, 'proactive_dismissed', { trigger: 'kayak', how: 'not_now', seconds_visible: 5 },
    NOW + 5_000)
  assert.equal(notNow.state, 'dismissed')
  assert.equal(cardAfter(notNow, 'proactive_engaged', { trigger: 'kayak', reply: 'channel', seconds_to_reply: 40 },
    NOW + 40_300).state, 'engaged')
})

test('an opening named by its own time: a second note a second later is not the card, whatever its seconds say (CodeRabbit #282, round 3)', () => {
  const first = cardAfter(null, 'proactive_shown', { trigger: 'note', surface: 'panel', opening_at: NOW - 3 }, NOW)
  assert.equal(first.openingAt, NOW - 3)
  assert.deepEqual(readVisitCard(cardToKeep(first)), first, 'kept with its opening for the visit’s next pages')
  // The first note closed at once, a second showed a second later and was answered 0.8 s after it: its rounded
  // second would put its opening within the card's two seconds, its own time does not.
  const closed = cardAfter(first, 'proactive_dismissed', { trigger: 'note', how: 'close', seconds_visible: 0,
    opening_at: NOW - 3 }, NOW + 200)
  assert.equal(closed.state, 'dismissed')
  assert.equal(cardAfter(closed, 'proactive_engaged', { trigger: 'note', reply: 'typed', seconds_to_reply: 1,
    opening_at: NOW + 997 }, NOW + 1_800), closed)
  assert.equal(cardAfter(first, 'proactive_engaged', { trigger: 'note', reply: 'typed', seconds_to_reply: 1,
    opening_at: NOW + 997 }, NOW + 1_800), first)
  // Its own answer names it, minutes later too: "Not now", the pill, then the reply.
  const kayak = cardAfter(null, 'proactive_shown', { trigger: 'kayak', surface: 'card', opening_at: NOW }, NOW)
  const notNow = cardAfter(kayak, 'proactive_dismissed', { trigger: 'kayak', how: 'not_now', seconds_visible: 5,
    opening_at: NOW }, NOW + 5_000)
  assert.equal(cardAfter(notNow, 'proactive_engaged', { trigger: 'kayak', reply: 'channel', seconds_to_reply: 300,
    opening_at: NOW }, NOW + 300_000).state, 'engaged')
  // The card's moment is the widget's own: every one of its events names its opening.
  const hook = readFileSync(join(here, '..', 'src', 'proactive.ts'), 'utf8')
  assert.equal((hook.match(/opening_at: opening\.shownAt,/g) ?? []).length, 2, 'the card’s and the note’s shown')
  assert.equal((hook.match(/opening_at: o\.shownAt,/g) ?? []).length, 2, 'dismissed and engaged')
})

test('a tab’s card is kept while the registry’s visit goes on: a new visit’s first card is the card (CodeRabbit #282, round 3)', () => {
  const MIN = 60_000
  const card = cardAfter(null, 'proactive_shown', { trigger: 'ads', surface: 'card' }, NOW)
  const kept = cardToKeep(card)
  // The card's snapshot, and the next page's a minute on, taken by bbc-live.
  let mark = afterSent(afterSent(readSentMark(null), NOW + 1_500), NOW + MIN)
  assert.deepEqual(mark, { last: NOW + MIN, before: 0 })
  assert.deepEqual(keptCardFor(kept, mark, NOW + 2 * MIN), card, 'the next page, a minute on: kept')
  // The live list's beats kept the browser's count of visits going; bbc-live took nothing for 31 minutes.
  assert.equal(keptCardFor(kept, mark, NOW + 32 * MIN), null, 'a page after 30 quiet minutes: it goes')
  // Another tab's snapshot 24 minutes on kept the same visit going.
  assert.deepEqual(keptCardFor(kept, afterSent(mark, NOW + 25 * MIN), NOW + 35 * MIN), card)
  // Another tab began the next visit after 31 quiet minutes: this tab's card, reloaded, is the last visit's.
  const next = afterSent(mark, NOW + 32 * MIN)
  assert.deepEqual(next, { last: NOW + 32 * MIN, before: NOW + MIN })
  assert.equal(keptCardFor(kept, next, NOW + 33 * MIN), null, 'the last visit’s card, whichever tab began the next')
  // A card shown in the quiet half hour rides the snapshot that begins the next visit: it is that visit's card.
  const late = cardAfter(null, 'proactive_shown', { trigger: 'ads', surface: 'card' }, NOW + 31 * MIN)
  assert.ok(cardStillOn(late, next, NOW + 33 * MIN))
  // A snapshot answered late, after a newer one was taken, changes nothing.
  assert.equal(afterSent(next, NOW + 31 * MIN), next)
  // The card's own answer is heard of too: a snapshot follows it.
  mark = afterSent(readSentMark(null), NOW + 1_500)
  const answered = cardAfter(card, 'proactive_engaged', { trigger: 'ads', reply: 'yes' }, NOW + 20 * MIN)
  assert.ok(cardStillOn(answered, mark, NOW + 49 * MIN))
  assert.ok(!cardStillOn(answered, mark, NOW + 50 * MIN))
  for (const raw of [null, '', '{', JSON.stringify({ visit: 3, card: { state: 'loved' } })]) {
    assert.equal(keptCardFor(raw, mark, NOW), null, String(raw))
  }
  for (const raw of [null, '', '{', '[]', '{"last":"x","before":-4}']) {
    assert.deepEqual(readSentMark(raw), { last: 0, before: 0 }, String(raw))
  }
  assert.deepEqual(readSentMark(JSON.stringify(next)), next)
})

test('one snapshot waits at a time, and the stop leaves none behind (CodeRabbit #282, round 3)', () => {
  const waiting = new Map()
  let ids = 0
  const set = (fn, ms) => { ids++; waiting.set(ids, { fn, ms }); return ids }
  const clear = (id) => { waiting.delete(id) }
  const fire = () => { const [[id, t]] = [...waiting]; waiting.delete(id); t.fn() }
  let runs = 0
  const snap = snapshotTimer(() => { runs++ }, set, clear)
  snap.soon(1500)
  snap.soon(1500)
  assert.equal(waiting.size, 1, 'an event after an event: one snapshot waits')
  snap.now()
  assert.equal(runs, 1)
  assert.equal(waiting.size, 0, 'the minute’s snapshot, sent now, takes the waiting one with it')
  snap.soon(1500)
  assert.ok(snap.waiting())
  fire()
  assert.equal(runs, 2)
  assert.ok(!snap.waiting())
  snap.soon(1500)
  snap.cancel()
  assert.equal(waiting.size, 0, 'the teardown: nothing fires after the widget is gone')
  assert.ok(!snap.waiting())
  assert.equal(runs, 2)
})

test('a snapshot carries its number on the page, so bbc-live keeps the newest (CodeRabbit #282, round 3)', () => {
  const base = { pageId: 'pabc123def456g', path: '/', pageMs: 0, seenMs: 0, trip: null, utm: {}, referrer: '',
    siteHost: '', phone: false, site: 'bbc', events: [], steps: '', card: null, hasChat: false, nowMs: NOW }
  assert.equal(visitBody({ ...base, seq: 7 }).seq, 7)
  assert.equal(visitBody({ ...base, seq: MAX_SEQ + 5 }).seq, MAX_SEQ)
  for (const seq of [undefined, 0, -1, 1.5]) assert.equal(visitBody({ ...base, seq }).seq, undefined, String(seq))
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
  assert.match(entry, /const stopVisits = embedded \? null : startVisits\(\{ apiUrl, site: brand\.id \}\)/)
  // It goes with the widget: the teardown stops its timers, listeners and tap (CodeRabbit #282, round 3).
  const teardown = entry.slice(entry.indexOf('__BBC_WIDGET_TEARDOWN__ = () => {'))
  assert.match(teardown, /stopSiteSignals\(\)\s+\/\/[^\n]*\n\s+stopVisits\?\.\(\)/)
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

test('the registry’s stop, the fresh pass after a refusal, and the visit kept going (CodeRabbit #282, round 3)', () => {
  const visits = readFileSync(join(here, '..', 'src', 'visits.ts'), 'utf8')
  // A refused pass: asked for again, not the page's first answer kept by pageConfig.
  assert.match(visits, /res\.status === 401 && Date\.now\(\) - renewedAt > RENEW_MS\) \{\s+renewedAt = Date\.now\(\)\s+askConfig\(apiUrl\)/)
  // Every snapshot keeps the browser's visit going; a card whose registry visit ended is dropped first.
  assert.match(visits, /const goingOn = \(\) => \{\s+noteVisit\(\)\s+if \(!card \|\| cardStillOn\(card, sentMark\(\), Date\.now\(\)\)\) return\s+card = null/)
  assert.equal((visits.match(/sends\+\+\n\s+goingOn\(\)/g) ?? []).length, 2, 'a snapshot by fetch and the page’s last one')
  assert.match(visits, /let card: VisitCard \| null = keptCardFor\(ssGet\(CARD_KEY\), sentMark\(\), Date\.now\(\)\)/)
  // Only a snapshot bbc-live took marks the visit: a 2xx, or the page's last one queued by sendBeacon; never before
  // the answer, so one lost offline or refused leaves the mark where it was.
  assert.equal((visits.match(/markSent\(sentAt\)/g) ?? []).length, 3)
  assert.equal((visits.match(/if \(res\.ok\) markSent\(sentAt\)/g) ?? []).length, 2)
  assert.match(visits, /sendBeacon\?\.\([^\n]+\)\) \{\n\s+markSent\(sentAt\)\n\s+return/)
  assert.ok(!/lsSet\(SENT_KEY/.test(visits.replace(/function markSent[\s\S]*?\n\}/, '')), 'the mark is written by markSent alone')
  assert.match(visits, /conversationId: conversationId\(\), nowMs: now, pass, seq: \+\+seq,/)
  // One snapshot waits at a time, through the page's one timer.
  assert.match(visits, /const snap = snapshotTimer\(send\)/)
  assert.match(visits, /if \(t\) snap\.soon\(AFTER_EVENT_MS\)/)
  assert.ok(!/setTimeout\(send/.test(visits), 'no snapshot timer outside snapshotTimer')
  // The minute's loop stops once the page's snapshots run out, and the stop takes everything down.
  assert.match(visits, /if \(sends < MAX_SENDS\) everyTimer = setTimeout\(every, EVERY_MS\)/)
  const stop = visits.slice(visits.indexOf('const stop = () => {'), visits.indexOf('stopCurrent = stop'))
  for (const part of [/snap\.cancel\(\)/, /clearTimeout\(everyTimer\)/, /removeEventListener\('visibilitychange', onVisibility\)/,
    /removeEventListener\('pagehide', onPageHide\)/, /offCapture\(tap\)/]) {
    assert.match(stop, part)
  }
})

test('the config and its pass go with the visitor they were asked for (CodeRabbit #282, round 3)', () => {
  const cfg = readConfig({ enabled: false, url: 'https://live.example.test', pass: PASS, visits: true })
  const kept = { cfg, visitorId: 'b1d6f0a2-3c4e-4f5a-8b9c-0d1e2f3a4b5c' }
  assert.equal(configFor(kept, 'b1d6f0a2-3c4e-4f5a-8b9c-0d1e2f3a4b5c'), cfg)
  // A form for someone else gave this browser a new visitor id: the last one's pass carries nothing of theirs.
  assert.equal(configFor(kept, 'c2e7a1b3-4d5f-4a6b-9c0d-1e2f3a4b5c6d'), null)
  assert.equal(configFor(kept, null), null)
  assert.equal(configFor(null, 'b1d6f0a2-3c4e-4f5a-8b9c-0d1e2f3a4b5c'), null)
  const live = readFileSync(join(here, '..', 'src', 'live.ts'), 'utf8')
  assert.match(live, /export function currentConfig\(\): LiveConfig \| null \{ return configFor\(latest, getVisitorId\(\)\) \}/)
  assert.match(live, /if \(!cfg\.later && ensureVisitorId\(\) === visitorId\) latest = \{ cfg, visitorId \}/)
})
