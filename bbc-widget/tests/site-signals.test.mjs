/**
 * What the site tells the chat (bbc-signals.astro) and whose PostHog the widget sends to.
 *
 * Pure parts tested directly (siteSignals.ts, tripContext.ts, posthogChoice.ts); the store against a fake
 * window (Node's EventTarget and CustomEvent); the wiring in entry.tsx, proactive.ts, ChatWindow.tsx and
 * posthog.ts pinned on the source.
 *
 * Plain .mjs + strip-types import: the widget's zero-dep test pattern.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  FIELD_KINDS, SITE_EVENTS, activityLine, activityToken, applySiteEvent, fieldKind, fieldKinds, lastSearch,
  noMomentPage, onSiteEvent, readSiteEvent, readSitePage, siteEvents, siteMetadata, sitePage, startSiteSignals,
  stopSiteSignals,
} from '../src/siteSignals.ts'
import { pickTrip, tripFromFields, tripFromTitle, tripFromUrl } from '../src/tripContext.ts'
import { choosePostHog, sitePostHogOf, watchSitePostHog } from '../src/posthogChoice.ts'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const read = (f) => readFileSync(join(SRC, f), 'utf8')
const ev = (name, detail = {}, at = 1_000) => readSiteEvent(name, detail, at, 10_000)

// ── window.bbcPage ────────────────────────────────────────────────────────────────────────────────────

test('bbcPage is read field by field: a wrong site or page type is dropped, never trusted', () => {
  assert.deepEqual(
    readSitePage({ site: 'bbc', page_type: 'route', trip: { origin_code: 'JFK' } }),
    { site: 'bbc', pageType: 'route', trip: { origin_code: 'JFK' } },
  )
  assert.deepEqual(readSitePage({ site: 'xyz', page_type: 'route' }), { site: null, pageType: 'route', trip: null })
  assert.deepEqual(readSitePage({ site: 'bct', page_type: 'checkout' }), { site: 'bct', pageType: null, trip: null })
  assert.equal(readSitePage({ trip: ['JFK'] }), null)
  for (const junk of [null, undefined, 'route', 42, []]) assert.equal(readSitePage(junk), null)
})

// ── window.bbcQueue and bbc:* events ──────────────────────────────────────────────────────────────────

test('only the events of the contract are read; a bad time reads as now', () => {
  assert.equal(SITE_EVENTS.length, 8)
  assert.equal(readSiteEvent('page_view', {}, 1, 10), null)
  assert.equal(readSiteEvent(42, {}, 1, 10), null)
  assert.deepEqual(readSiteEvent('quote_form_opened', { form: 'main' }, 5, 10), { name: 'quote_form_opened', detail: { form: 'main' }, at: 5 })
  assert.equal(readSiteEvent('quote_form_opened', null, 5, 10).detail.constructor, Object)
  for (const at of [undefined, 'soon', -1, NaN, 11]) assert.equal(readSiteEvent('quote_form_opened', {}, at, 10).at, 10)
})

test('an event keeps only the fields its kind may carry, checked on arrival: what the visitor typed is dropped', () => {
  const raw = {
    origin_code: 'jfk', destination_code: 'LHR', origin_name: 'New York (NYC)', destination_name: 'São Paulo',
    depart_date: '2027-03-15', return_date: '15/03/2027', trip_type: 'round_trip', cabin: 'business',
    adults: '2', children: 0, infants: 12,
    name: 'Ann Smith', email: 'ann@example.com', phone: '+1 212 555 0187', message: 'call me after 5',
  }
  const e = readSiteEvent('search_submitted', raw, 5, 10)
  assert.deepEqual(e.detail, {
    origin_code: 'JFK', destination_code: 'LHR', origin_name: 'New York (NYC)', destination_name: 'São Paulo',
    depart_date: '2027-03-15', trip_type: 'round_trip', cabin: 'business', adults: 2, children: 0,
  })
  // A place that is no place: an email, a number, a sentence.
  for (const v of ['ann@example.com', '+1 212 555 0187', 'call me after 5 please, I am busy until then', 'x']) {
    assert.equal(readSiteEvent('search_submitted', { origin_name: v }, 5, 10).detail.origin_name, undefined, v)
  }
  // A fresh object: what the site changes in its own object later is not seen here.
  raw.origin_code = 'BOS'
  assert.equal(e.detail.origin_code, 'JFK')
  const detail = (name, d) => readSiteEvent(name, d, 5, 10).detail
  assert.deepEqual(detail('quote_form_opened', { form: 'popup', text: 'Ann' }), { form: 'popup' })
  assert.deepEqual(detail('quote_form_opened', { form: 'Ann' }), {})
  assert.deepEqual(detail('quote_form_step', { step: 2, value: 'Ann' }), { step: 2 })
  assert.deepEqual(detail('quote_form_step', { step: '2' }), {})
  assert.deepEqual(detail('quote_form_invalid', { fields: ['phone_number', 'Ann Smith'], values: ['Ann'] }), { fields: ['phone', 'other'] })
  assert.deepEqual(detail('quote_form_failed', { reason: 'server', error: 'ann@example.com rejected' }), { reason: 'server' })
  assert.deepEqual(detail('quote_form_failed', { reason: 'ann@example.com' }), { reason: 'other' })
  assert.deepEqual(detail('contact_clicked', { channel: 'whatsapp', href: 'https://wa.me/12125550187' }), { channel: 'whatsapp' })
  assert.deepEqual(detail('quote_form_submitted', { email: 'ann@example.com' }), {})
  assert.deepEqual(detail('quote_form_submitting', { phone: '+1 212 555 0187' }), {})
})

test('a refused send keeps what kind of field it was, in a fixed list of words: never a value', () => {
  assert.deepEqual(
    fieldKinds(['phone_number', 'Email', 'tel', 'first_name', 'depart_date', 'to']),
    ['phone', 'email', 'name', 'dates', 'destination'],
    'each kind once, in the order the site sent them',
  )
  assert.deepEqual(
    fieldKinds(['name', 'email', 'phone', 'from', 'to', 'date', 'adults', 'cabin']),
    ['name', 'email', 'phone', 'origin', 'destination', 'dates'],
    'six at most',
  )
  const names = {
    from: 'origin', origin_code: 'origin', departure_airport: 'origin', destination_name: 'destination',
    return_date: 'dates', passengers: 'passengers', travellers: 'passengers', infants: 'passengers',
    cabin_class: 'cabin', class: 'cabin', trip_type: 'trip_type', comments: 'message', message: 'message',
    agree_terms: 'consent', 'g-recaptcha-response': 'captcha', whatsapp: 'phone', full_name: 'name',
    budget: 'other',
  }
  for (const [name, kind] of Object.entries(names)) assert.equal(fieldKind(name), kind, name)
  // What the visitor typed, sent by mistake, becomes a word of the list or nothing, never itself.
  assert.deepEqual(fieldKinds(['Ann', 'New York', 'ann@example.com', 'Toronto']), ['other'])
  assert.deepEqual(fieldKinds(['+1 212 555 0187', '2027-03-15', '', 7, null, {}]), [])
  assert.deepEqual(fieldKinds('phone'), [])
  for (const v of ['Ann Smith', 'ann.mail@example.com', '+44 20 7946 0958', 'hotel']) {
    const k = fieldKind(v)
    assert.ok(k === null || FIELD_KINDS.includes(k), v)
  }
})

test('the site\'s thank-you and legal pages get no moment, whatever their address', () => {
  assert.equal(noMomentPage({ site: 'bbc', pageType: 'thank_you', trip: null }), true)
  assert.equal(noMomentPage({ site: 'bct', pageType: 'legal', trip: null }), true)
  for (const t of ['route', 'quote', 'home', null]) assert.equal(noMomentPage({ site: 'bbc', pageType: t, trip: null }), false)
  assert.equal(noMomentPage(null), false)
})

test('the line for the server says what the visitor did, never what they typed', () => {
  assert.equal(activityToken(ev('search_submitted', {
    origin_code: 'jfk', destination_code: 'LHR', depart_date: '2027-03-15', name: 'Ann', email: 'ann@example.com',
  })), 'search:JFK-LHR:2027-03-15')
  assert.equal(activityToken(ev('search_submitted', { destination_code: 'LHR' })), 'search:?-LHR')
  assert.equal(activityToken(ev('search_submitted', { origin_code: 'New York' })), 'search')
  assert.equal(activityToken(ev('quote_form_opened')), 'quote_opened')
  assert.equal(activityToken(ev('quote_form_step', { step: 2 })), 'quote_step:2')
  assert.equal(activityToken(ev('quote_form_step', { step: '2' })), 'quote_step')
  assert.equal(activityToken(ev('quote_form_invalid', { fields: ['phone', 'email'] })), 'quote_invalid:phone|email')
  assert.equal(activityToken(ev('quote_form_invalid', { fields: ['+1 212 555 0187'] })), 'quote_invalid')
  assert.equal(activityToken(ev('quote_form_invalid', { fields: ['Ann', 'phone'] })), 'quote_invalid:other|phone')
  assert.equal(activityToken(ev('quote_form_submitting')), 'quote_submitting')
  assert.equal(activityToken(ev('quote_form_failed', { reason: 'captcha' })), 'quote_failed:captcha')
  assert.equal(activityToken(ev('quote_form_failed', { reason: 'boom' })), 'quote_failed:other')
  assert.equal(activityToken(ev('quote_form_submitted')), 'quote_submitted')
  assert.equal(activityToken(ev('contact_clicked', { channel: 'whatsapp' })), 'contact:whatsapp')
  assert.equal(activityToken(ev('contact_clicked', { channel: 'tel:+12125550187' })), 'contact')

  const steps = [ev('quote_form_opened'), ev('quote_form_opened'), ev('quote_form_invalid', { fields: ['phone'] }), ev('quote_form_opened')]
  assert.equal(activityLine(steps), 'quote_opened;quote_invalid:phone;quote_opened')
  const many = Array.from({ length: 20 }, (_, i) => ev('quote_form_step', { step: (i % 20) + 1 }))
  assert.equal(activityLine(many).split(';').length, 12)
  assert.equal(activityLine(many).split(';')[11], 'quote_step:20')
  assert.ok(activityLine(many, 50).length <= 300)
  assert.equal(activityLine([]), '')
})

// ── The quote moment from the site's word ─────────────────────────────────────────────────────────────

test('a refused or failed send is a press with a flagged field; a send in flight holds the moment', () => {
  let fx = applySiteEvent(null, ev('quote_form_opened', {}, 1_000))
  assert.deepEqual(fx, { press: null, stop: null }, 'opening the form is no press')
  fx = applySiteEvent(null, ev('quote_form_invalid', { fields: ['phone'] }, 2_000))
  assert.deepEqual(fx.press, { at: 2_000, activityAt: 2_000, invalid: true, busy: false })
  fx = applySiteEvent(fx.press, ev('quote_form_step', {}, 3_000))
  assert.deepEqual(fx.press, { at: 2_000, activityAt: 3_000, invalid: true, busy: false }, 'working on it: not quiet')
  fx = applySiteEvent(fx.press, ev('quote_form_submitting', {}, 4_000))
  assert.equal(fx.press.busy, true)
  fx = applySiteEvent(fx.press, ev('quote_form_failed', { reason: 'server' }, 5_000))
  assert.deepEqual(fx.press, { at: 2_000, activityAt: 5_000, invalid: true, busy: false })
  assert.deepEqual(applySiteEvent(fx.press, ev('quote_form_submitted')), { press: null, stop: 'sent' })
  assert.equal(applySiteEvent(null, ev('contact_clicked', { channel: 'phone' })).stop, 'contact')
  assert.deepEqual(applySiteEvent(fx.press, ev('search_submitted')), { press: fx.press, stop: null })
})

// ── The trip ──────────────────────────────────────────────────────────────────────────────────────────

test('a trip the site hands over as fields follows the address\'s rules; numbers count', () => {
  assert.deepEqual(
    tripFromFields({
      origin_code: 'jfk', destination_code: 'LHR', origin_name: 'New York', destination_name: 'London',
      depart_date: '2027-03-15', return_date: '2027-03-22', trip_type: 'round_trip', cabin: 'business',
      adults: 2, children: '1', infants: 0,
    }, 'route_page'),
    {
      source: 'route_page', origin_code: 'JFK', destination_code: 'LHR', origin_name: 'New York',
      destination_name: 'London', depart_date: '2027-03-15', return_date: '2027-03-22', trip_type: 'round_trip',
      cabin: 'business', adults: 2, children: 1, infants: 0,
    },
  )
  assert.deepEqual(
    tripFromFields({ origin_code: 'NEW YORK', destination_code: 'LHR', depart_date: '15/03/2027', adults: 200, cabin: '<b>' }, 'search'),
    { source: 'search', destination_code: 'LHR' },
  )
  assert.equal(tripFromFields({ cabin: 'business', adults: 2 }, 'search'), null, 'no place, no trip')
  for (const junk of [null, undefined, 'JFK', [], 7]) assert.equal(tripFromFields(junk, 'search'), null)
})

test('the newest word wins: the site search, the address, the page, the title', () => {
  const search = tripFromFields({ origin_code: 'BOS', destination_code: 'CDG' }, 'search')
  const url = tripFromUrl('?from=JFK&to=LHR&utm_source=kayak')
  const page = tripFromFields({ origin_code: 'MIA', destination_code: 'FCO' }, 'route_page')
  const title = tripFromTitle('Chicago to Tokyo business class flights')
  assert.equal(pickTrip(search, url, page, title), search)
  assert.equal(pickTrip(null, url, page, title), url)
  assert.equal(pickTrip(null, null, page, title), page)
  assert.equal(pickTrip(null, null, null, title), title)
  assert.equal(pickTrip(null, undefined, null), null)
})

// ── The page's store ──────────────────────────────────────────────────────────────────────────────────

test('the store reads the queue the page kept before the widget, then listens; stop detaches', () => {
  const win = new EventTarget()
  win.bbcPage = { site: 'bbc', page_type: 'route', trip: { origin_code: 'JFK', destination_code: 'LHR' } }
  win.bbcQueue = [
    { name: 'quote_form_opened', detail: { form: 'main' }, at: 1 },
    { name: 'not_ours', detail: {}, at: 2 },
    'junk',
    { name: 'search_submitted', detail: { origin_code: 'JFK', destination_code: 'LHR', depart_date: '2027-03-15' }, at: 3 },
  ]
  startSiteSignals(win)
  assert.deepEqual(siteEvents().map(e => e.name), ['quote_form_opened', 'search_submitted'])
  assert.deepEqual(sitePage(), { site: 'bbc', pageType: 'route', trip: { origin_code: 'JFK', destination_code: 'LHR' } })
  assert.equal(lastSearch().depart_date, '2027-03-15')

  const heard = []
  const off = onSiteEvent(e => heard.push(e.name))
  win.dispatchEvent(new CustomEvent('bbc:quote_form_invalid', { detail: { fields: ['phone'] } }))
  win.dispatchEvent(new CustomEvent('bbc:page_view', { detail: {} }))
  assert.deepEqual(heard, ['quote_form_invalid'])
  assert.deepEqual(siteMetadata(), {
    page_type: 'route',
    page_activity: 'quote_opened;search:JFK-LHR:2027-03-15;quote_invalid:phone',
  })
  off()
  win.dispatchEvent(new CustomEvent('bbc:contact_clicked', { detail: { channel: 'phone' } }))
  assert.deepEqual(heard, ['quote_form_invalid'], 'a listener that stopped hears nothing more')
  assert.equal(siteEvents().length, 4)

  // The queue's search kept its trip, never the rest of what the site put in it.
  win.bbcQueue.push({ name: 'search_submitted', detail: { origin_code: 'BOS', email: 'ann@example.com' }, at: 4 })
  startSiteSignals(win)
  assert.deepEqual(lastSearch(), { origin_code: 'BOS' })
  assert.doesNotMatch(JSON.stringify(siteEvents()), /ann@example\.com/)

  // The widget's teardown: nothing heard after it, and nothing the site said kept.
  stopSiteSignals()
  win.dispatchEvent(new CustomEvent('bbc:quote_form_submitted', { detail: {} }))
  assert.deepEqual(siteEvents(), [], 'after stop the page is no longer heard, nor remembered')
  assert.equal(sitePage(), null)
  assert.deepEqual(siteMetadata(), {})

  // A page without bbc-signals: nothing, and nothing breaks.
  startSiteSignals(new EventTarget())
  assert.equal(sitePage(), null)
  assert.deepEqual(siteEvents(), [])
  assert.deepEqual(siteMetadata(), {})
  stopSiteSignals()
})

test('the queue keeps the last 50; a listener that throws stops nobody else', () => {
  const win = new EventTarget()
  win.bbcQueue = Array.from({ length: 70 }, (_, i) => ({ name: 'quote_form_step', detail: { step: (i % 20) + 1 }, at: i + 1 }))
  startSiteSignals(win)
  assert.equal(siteEvents().length, 50)
  const heard = []
  onSiteEvent(() => { throw new Error('boom') })
  onSiteEvent(e => heard.push(e.name))
  win.dispatchEvent(new CustomEvent('bbc:quote_form_opened', { detail: {} }))
  assert.deepEqual(heard, ['quote_form_opened'])
  assert.equal(siteEvents().length, 50)
  stopSiteSignals()
})

// ── Whose PostHog ─────────────────────────────────────────────────────────────────────────────────────

test('the site\'s PostHog once it is on; the widget\'s own only with consent; none without', () => {
  assert.equal(choosePostHog({ siteOn: true, consent: false, configured: false }), 'site')
  assert.equal(choosePostHog({ siteOn: true, consent: true, configured: true }), 'site')
  assert.equal(choosePostHog({ siteOn: false, consent: true, configured: true }), 'own')
  assert.equal(choosePostHog({ siteOn: false, consent: false, configured: true }), 'none')
  assert.equal(choosePostHog({ siteOn: false, consent: true, configured: false }), 'none')
})

test('a window.posthog counts as the site\'s only when the site said it started it', () => {
  const ph = { capture() {} }
  assert.equal(sitePostHogOf({ bbcPostHogOn: true, posthog: ph }), ph)
  // Another script's PostHog, or the site's before consent: never used.
  assert.equal(sitePostHogOf({ posthog: ph }), undefined)
  assert.equal(sitePostHogOf({ bbcPostHogOn: 'yes', posthog: ph }), undefined)
  // The posthog-js stub before it loads has no capture yet.
  assert.equal(sitePostHogOf({ bbcPostHogOn: true, posthog: {} }), undefined)
  assert.equal(sitePostHogOf({ bbcPostHogOn: true }), undefined)
  for (const w of [null, undefined, 7]) assert.equal(sitePostHogOf(w), undefined)
  const hostile = new Proxy({}, { get() { throw new Error('blocked') } })
  assert.equal(sitePostHogOf(hostile), undefined)
})

test('the widget\'s copy goes quiet when the site\'s PostHog comes on, with no event of the widget between', (t) => {
  t.mock.timers.enable({ apis: ['setInterval'] })
  const win = {}
  let quieted = 0
  watchSitePostHog(win, () => { quieted++ }, 1000, 60_000)
  t.mock.timers.tick(5000)
  assert.equal(quieted, 0, 'no PostHog on the site yet')
  win.posthog = { capture() {} }
  t.mock.timers.tick(2000)
  assert.equal(quieted, 0, 'a window.posthog without the site\'s word is not the site\'s')
  win.bbcPostHogOn = true
  t.mock.timers.tick(1000)
  assert.equal(quieted, 1, 'the next look sees it')
  t.mock.timers.tick(10_000)
  assert.equal(quieted, 1, 'once')

  // Bounded: after `forMs` it stops looking (the widget's next event still moves to the site's then).
  const late = {}
  let lateQuieted = 0
  watchSitePostHog(late, () => { lateQuieted++ }, 1000, 3000)
  t.mock.timers.tick(3000)
  Object.assign(late, { bbcPostHogOn: true, posthog: { capture() {} } })
  t.mock.timers.tick(5000)
  assert.equal(lateQuieted, 0)

  // Stopped by hand, and a quiet that throws breaks nothing.
  const gone = { bbcPostHogOn: true, posthog: { capture() {} } }
  let goneQuieted = 0
  watchSitePostHog(gone, () => { goneQuieted++ }, 1000, 60_000)()
  t.mock.timers.tick(5000)
  assert.equal(goneQuieted, 0)
  watchSitePostHog(gone, () => { throw new Error('boom') }, 1000, 60_000)
  t.mock.timers.tick(1000)
})

test('the widget\'s own PostHog starts only in startPostHog, never at import; reset never touches the site\'s', () => {
  const ph = read('posthog.ts')
  assert.equal(ph.match(/posthogSdk\.init\(/g).length, 1)
  const start = ph.slice(ph.indexOf('export function startPostHog'), ph.indexOf('/** The PostHog in use right now.'))
  assert.match(start, /posthogSdk\.init\(posthogKey, \{/)
  assert.match(start, /if \(choice !== 'own' \|\| !posthogKey \|\| !posthogHost\) \{/)
  assert.doesNotMatch(ph, /export const posthog = \(\(\) =>/)
  // The site's PostHog is recognised by posthogChoice.ts alone (tested above).
  assert.match(ph, /return typeof window === 'undefined' \? undefined : sitePostHogOf\(window\)/)
  // A new person in this browser resets the widget's own copy only.
  assert.match(ph, /reset\(\): void \{\s*try \{ if \(ownStarted && !sitePostHog\(\)\) posthogSdk\.reset\(\) \}/)
  // The copy that was running when the site's came on stops capturing by itself: nothing twice. It goes
  // quiet as soon as the site's is on (the watch above), and at the latest at the widget's next event.
  assert.match(ph, /posthogSdk\.set_config\(\{ autocapture: false, capture_pageview: false, capture_pageleave: false \}\)/)
  assert.match(ph, /posthogSdk\.stopSessionRecording\(\)/)
  assert.match(start, /ownStarted = true\s+\/\/[^\n]*\n\s+\/\/[^\n]*\n\s+if \(typeof window !== 'undefined'\) watchSitePostHog\(window, quietOwnCopy\)/)
  assert.match(ph, /function quietOwnCopy\(\): void \{\s+if \(!ownStarted \|\| ownQuiet\) return\s+ownQuiet = true/)
  assert.match(ph, /if \(site\) \{\s+quietOwnCopy\(\)\s+return site\s+\}/)
})

// ── The wiring ────────────────────────────────────────────────────────────────────────────────────────

test('entry starts the signals before the widget draws, and stops them on teardown', () => {
  const entry = read('entry.tsx')
  const start = entry.indexOf('startSiteSignals(window)')
  assert.ok(start > entry.indexOf('startPostHog({ consent: attribution })'))
  assert.ok(start < entry.indexOf('render(<Widget'))
  const teardown = entry.slice(entry.indexOf('__BBC_WIDGET_TEARDOWN__ = () => {'))
  assert.match(teardown, /root\.remove\(\)\s+stopSiteSignals\(\)/)
})

test('the card and the note start from the newest trip; a visitor who sent the form or reached out sees no moment', () => {
  const hook = read('proactive.ts')
  assert.equal(hook.match(/const trip = currentTrip\(\)/g).length, 2, 'ask and noteOpening')
  assert.match(hook, /return pickTrip\(\s*tripFromFields\(lastSearch\(\), 'search'\),\s*tripFromUrl\(search, kayakVisit\(\)\),\s*tripFromFields\(page\?\.trip, pageSource\),\s*tripFromTitle\(pageTitle\(\)\),\s*\)/)
  // The stop: no moment and no note for the rest of the visit, the queue before the widget included.
  const stop = hook.slice(hook.indexOf('const stopFor = (e: SiteEvent) => {'))
  assert.match(stop, /if \(!ssGet\(DONE_KEY\)\) ssSet\(DONE_KEY, stop\)\s+ssSet\(ATTENTION_KEY, '1'\)/)
  // …and a 10 s note already scheduled or on screen goes away (Widget shows it only while not quiet).
  assert.match(stop, /ssSet\(ATTENTION_KEY, '1'\)\s+\/\/[^\n]*\n\s+setQuiet\(true\)/)
  assert.match(read('Widget.tsx'), /showAttention=\{showAttention && !sessionActive && !proactive\.quiet\}/)
  // …and so does a card still waiting for an answer.
  assert.match(stop, /setQuiet\(true\)\s+\/\/[^\n]*\n\s+setOffer\(null\)/)
  // The site's thank-you or legal page: nothing opens by itself, whatever the address; also when the page
  // became one while the server's answer was on its way.
  assert.match(hook, /if \(ssGet\(NOTE_CLOSED_KEY\)\) return\s+\/\/[^\n]*\n\s+if \(noMomentPage\(sitePage\(\)\)\) return/)
  assert.match(hook, /if \(blockedPath\(location\.pathname\)\) return 'left_page'\s+\/\/[^\n]*\n\s+if \(noMomentPage\(sitePage\(\)\)\) return 'left_page'/)
  assert.match(stop, /for \(const e of siteEvents\(\)\) stopFor\(e\)\s+return onSiteEvent\(stopFor\)/)
  // The watch hears the queue first, then the page; and lets go of it on cleanup.
  assert.match(hook, /for \(const e of siteEvents\(\)\) onSite\(e\)\s+const offSite = onSiteEvent\(onSite\)/)
  assert.match(hook, /clearInterval\(tick\)\s+offSite\(\)/)
  assert.match(hook, /if \(fired \|\| disposed \|\| stopped \|\| document\.visibilityState !== 'visible'\) return/)
  assert.match(hook, /site_signals: !!sitePage\(\),/)
})

test('the conversation carries the page and the visitor\'s steps, with the PostHog session', () => {
  const chat = read('ChatWindow.tsx')
  assert.match(chat, /return \{ \.\.\.\(metadata \|\| \{\}\), \.\.\.\(sid \? \{ posthog_session_id: sid \} : \{\}\), \.\.\.siteMetadata\(\) \}/)
  // Both doors that create the conversation send it.
  assert.equal(chat.match(/metadata: withSession\(metadata\),/g).length, 2)
})
