/**
 * The chat that opens by itself, with the visitor's own search and no form.
 *
 * Pure parts tested directly (tripContext.ts, triggers.ts); the wiring in proactive.ts, ChatWindow.tsx,
 * FloatingButtons.tsx and Widget.tsx is pinned on the source (no DOM in these tests). The server's half
 * is bbc-chatbot-api/tests/test_proactive.py.
 *
 * Plain .mjs + strip-types import: the widget's zero-dep test pattern.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { hasContext, pickTrip, tripFromTitle, tripFromUrl } from '../src/tripContext.ts'

// The address, then the title: what a page without bbc-signals gives (proactive.ts currentTrip puts the
// site's search before the address and the page's own trip before the title; site-signals.test.mjs).
const readTrip = (href, h1) => {
  let search = ''
  try { search = new URL(href).search } catch { /* not an address: the title alone */ }
  return pickTrip(tripFromUrl(search), tripFromTitle(h1))
}
import {
  ADS_VISIBLE_S, KAYAK_VISIBLE_S, OFFER_TIMEOUT_MS, QUOTE_IDLE_S, QUOTE_INVALID_IDLE_S, QUOTE_WATCH_S, READER_VISIBLE_S,
  SAME_SEND_MS, TRIGGER_OFF_ARM, WATCHED_FIRST_S, adVisit, blockedPath, inViewBeforeStartMs, isNotNow, noteWaitMs,
  offAfter, pauseRunning, pickTrigger, readOff, replyKind,
} from '../src/triggers.ts'
import { fetchProactiveOffer, offerMeasures, offerVariant, preconnect } from '../src/api.ts'
import { keepOpening, readKeptOpening } from '../src/keptOpening.ts'
import bbc from '../src/config/bbc.ts'
import bct from '../src/config/bct.ts'
import { ALEX_PHOTO } from '../src/alexPhoto.ts'
import { WIDGET_CSS } from '../src/styles.ts'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const read = (f) => readFileSync(join(SRC, f), 'utf8')
const chat = read('ChatWindow.tsx')
const widget = read('Widget.tsx')
const buttons = read('FloatingButtons.tsx')
const hook = read('proactive.ts')
const api = read('api.ts')
const top = read('PanelTop.tsx')

// ── The trip from the page ───────────────────────────────────────────────────────────

test('a Kayak landing reads whole', () => {
  const href = 'https://buybusinessclass.com/business-class-flights?trip_type=round_trip&cabin_class=first'
    + '&from=ATL&to=ICN&depart_at=2027-05-25&return_at=2027-06-28&adults=4&childrens=0&infants=0'
    + '&utm_source=kayak&utm_medium=cpc&utm_campaign=cfmob'
  assert.deepEqual(readTrip(href, ''), {
    source: 'kayak',
    origin_code: 'ATL',
    destination_code: 'ICN',
    depart_date: '2027-05-25',
    return_date: '2027-06-28',
    trip_type: 'round_trip',
    cabin: 'first',
    adults: 4,
    children: 0,
    infants: 0,
  })
})

test('a page of a Kayak visit without the tag in its own address is still Kayak', () => {
  assert.equal(tripFromUrl('?from=JFK&to=LHR&depart_at=2027-01-10', true).source, 'kayak')
  assert.equal(tripFromUrl('?from=JFK&to=LHR&depart_at=2027-01-10').source, 'search')
})

test('the flight page spells the airports fly_from / fly_to', () => {
  const t = tripFromUrl('?fly_from=mia&fly_to=cdg&depart_at=2027-02-01&adults=2&cabin_class=business&trip_type=one_way')
  assert.equal(t.origin_code, 'MIA')
  assert.equal(t.destination_code, 'CDG')
  assert.equal(t.trip_type, 'one_way')
  assert.equal(t.adults, 2)
})

test('the site search: names with their code, the leg 0 fields', () => {
  const t = tripFromUrl('?tripType=round_trip&from-0=Chicago+%28CHI%29&to-0=London+%28LON%29&date-0=2027-03-02'
    + '&from-0-code=CHI&to-0-code=LON&returnDate=2027-03-20&cabin=business&adults=1&children=1')
  assert.deepEqual(t, {
    source: 'search',
    origin_code: 'CHI',
    destination_code: 'LON',
    origin_name: 'Chicago',
    destination_name: 'London',
    depart_date: '2027-03-02',
    return_date: '2027-03-20',
    trip_type: 'round_trip',
    cabin: 'business',
    adults: 1,
    children: 1,
  })
})

test('route pages name the trip in their title', () => {
  assert.deepEqual(tripFromTitle('New York to London business class flights'), {
    source: 'route_page', origin_name: 'New York', destination_name: 'London', cabin: 'business',
  })
  assert.deepEqual(tripFromTitle('First class flights to Abu Dhabi'), {
    source: 'route_page', destination_name: 'Abu Dhabi', cabin: 'first',
  })
  assert.deepEqual(tripFromTitle('Business class flights from Chicago'), {
    source: 'route_page', origin_name: 'Chicago', cabin: 'business',
  })
  assert.equal(tripFromTitle('Emirates business class deals'), null)
  assert.equal(tripFromTitle(''), null)
})

test('the address wins over the title; nothing at all is null', () => {
  const t = readTrip('https://buybusinessclass.com/search?from-0-code=SFO&to-0-code=NRT', 'Chicago to Rome business class flights')
  assert.equal(t.destination_code, 'NRT')
  assert.equal(readTrip('https://buybusinessclass.com/about', 'About us'), null)
  assert.equal(readTrip('not a url', 'Business class flights to Paris').destination_name, 'Paris')
})

test('what is plainly not a trip never leaves the page', () => {
  const t = tripFromUrl('?from=newsletter&to=%3Cscript%3E&depart_at=next-week&adults=999&cabin=' + 'x'.repeat(40)
    + '&to-0=' + 'y'.repeat(60) + '&from-0-code=JFK')
  assert.deepEqual(t, { source: 'search', origin_code: 'JFK' })
  assert.equal(tripFromUrl('?utm_source=kayak'), null)
})

test('context means the card can name a place', () => {
  assert.equal(hasContext(null), false)
  assert.equal(hasContext({ source: 'search', origin_code: 'JFK' }), false)
  assert.equal(hasContext({ source: 'route_page', destination_name: 'Paris' }), true)
})

// ── The moments ──────────────────────────────────────────────────────────────────────

const base = {
  path: '/business-class-flights',
  kayak: false,
  ad: false,
  destination: false,
  visibleS: 0,
  watchedS: 60,
  off: [],
  sinceActivityS: 0,
  scrolledScreens: 0,
  phone: false,
  sinceSiteTypingS: null,
  quote: null,
  sinceQuoteFormS: null,
  exitIntent: false,
}
const at = (over) => pickTrigger({ ...base, ...over })
const pressed = (over) => ({ sincePressS: 10, sinceFormActivityS: 0, invalid: false, busy: false, ...over })

test('kayak: from Kayak and still here after 5 s in view', () => {
  assert.equal(at({ kayak: true, visibleS: KAYAK_VISIBLE_S - 0.5 }), null)
  assert.equal(at({ kayak: true, visibleS: KAYAK_VISIBLE_S }), 'kayak')
  assert.equal(at({ kayak: false, visibleS: 10 }), null)
})

test('reader: 25 s in view and half a screen scrolled', () => {
  assert.equal(at({ visibleS: READER_VISIBLE_S, scrolledScreens: 0.4 }), null)
  assert.equal(at({ visibleS: READER_VISIBLE_S - 1, scrolledScreens: 2 }), null)
  assert.equal(at({ visibleS: READER_VISIBLE_S, scrolledScreens: 0.5 }), 'reader')
})

test('leaving the page is no moment any more (owner, 17 Nov: 35 panels, no answer, opened as the tab closed)', () => {
  for (const visibleS of [15, 60, 300]) {
    assert.equal(at({ exitIntent: true, visibleS }), null, `${visibleS} s`)
    assert.equal(at({ exitIntent: true, visibleS, phone: true }), null, `${visibleS} s, phone`)
  }
  // It still ends a pressed quote form's wait (below), and the visitor's other moments still come.
  assert.equal(at({ exitIntent: true, visibleS: 40, scrolledScreens: 2 }), 'reader')
  assert.doesNotMatch(read('triggers.ts'), /return 'exit'/)
})

test('quote: the form went quiet after the press — 8 s, or 5 s after a flagged field', () => {
  assert.equal(at({ quote: pressed({ sinceFormActivityS: QUOTE_IDLE_S - 1 }) }), null)
  assert.equal(at({ quote: pressed({ sinceFormActivityS: QUOTE_IDLE_S }) }), 'quote')
  assert.equal(at({ quote: pressed({ sinceFormActivityS: QUOTE_INVALID_IDLE_S, invalid: true }) }), 'quote')
  assert.equal(at({ quote: pressed({ sinceFormActivityS: QUOTE_INVALID_IDLE_S, invalid: false }) }), null)
})

test('quote: leaving after a press is the moment at once; a send in flight never is', () => {
  assert.equal(at({ quote: pressed({ sinceFormActivityS: 1 }), exitIntent: true }), 'quote')
  assert.equal(at({ quote: pressed({ sinceFormActivityS: 30, busy: true }) }), null)
})

test('quote comes first, and a form being worked on holds every other moment', () => {
  // Kayak would have fired at 5 s; the visitor is filling the form: nothing.
  assert.equal(at({ kayak: true, visibleS: 30, scrolledScreens: 3, quote: pressed({ sinceFormActivityS: 2 }) }), null)
  assert.equal(at({ kayak: true, visibleS: 30, quote: pressed({ sinceFormActivityS: 9 }) }), 'quote')
  // Long after the press the form is part of the page again.
  assert.equal(at({ kayak: true, visibleS: 300, quote: pressed({ sincePressS: 200, sinceFormActivityS: 1 }) }), 'kayak')
  // …unless its send is still in flight: that holds every moment, however long it takes.
  assert.equal(at({ kayak: true, visibleS: 300, scrolledScreens: 3, quote: pressed({ sincePressS: 200, busy: true }) }), null)
  assert.equal(at({ exitIntent: true, visibleS: 300, quote: pressed({ sincePressS: 900, busy: true }) }), null)
})

test('kayak before reader', () => {
  assert.equal(at({ kayak: true, visibleS: 40, scrolledScreens: 2, exitIntent: true }), 'kayak')
  assert.equal(at({ visibleS: 40, scrolledScreens: 2, exitIntent: true }), 'reader')
})

// ── ads: a Google or Bing ad, on a page about one destination (owner, 18 Nov) ─────────────────────────

test('ads: from an ad, on a page that names a destination, still here after 5 s in view', () => {
  assert.equal(ADS_VISIBLE_S, 5)
  assert.equal(at({ ad: true, destination: true, visibleS: ADS_VISIBLE_S - 0.5 }), null)
  assert.equal(at({ ad: true, destination: true, visibleS: ADS_VISIBLE_S }), 'ads')
  // No destination on the page (the home page, the deals): no ads card; the reader may still come.
  assert.equal(at({ ad: true, destination: false, visibleS: 20 }), null)
  assert.equal(at({ ad: true, destination: false, visibleS: READER_VISIBLE_S, scrolledScreens: 1 }), 'reader')
  // Not from an ad: a destination page alone is no moment.
  assert.equal(at({ ad: false, destination: true, visibleS: 20 }), null)
})

test('ads keeps the order and the holds of the other moments', () => {
  // Kayak first, then ads, then the reader.
  assert.equal(at({ kayak: true, ad: true, destination: true, visibleS: 40, scrolledScreens: 2 }), 'kayak')
  assert.equal(at({ ad: true, destination: true, visibleS: 40, scrolledScreens: 2 }), 'ads')
  // The quote form being worked on, typing on the site, an idle visitor, the thank-you page: nothing.
  const ad = { ad: true, destination: true, visibleS: 30 }
  assert.equal(at({ ...ad, sinceQuoteFormS: 5 }), null)
  assert.equal(at({ ...ad, sinceSiteTypingS: 1 }), null)
  assert.equal(at({ ...ad, sinceActivityS: 61 }), null)
  assert.equal(at({ ...ad, path: '/thank-you' }), null)
  // A press of the quote form still comes first, once the form goes quiet.
  assert.equal(at({ ...ad, quote: pressed({ sinceFormActivityS: QUOTE_IDLE_S }) }), 'quote')
})

test('an ad visit: the click id Google or Bing adds, or their tags with a paid medium; never Kayak, never organic', () => {
  assert.equal(adVisit({ gclid: 'Cj0KCQ', utm_source: 'google', utm_medium: 'cpc' }), true)
  assert.equal(adVisit({ msclkid: 'f3f2cf0d', utm_source: 'bing', utm_medium: 'cpc' }), true)
  // The click id alone, and the odd tags seen with it ("google " as the medium too).
  assert.equal(adVisit({ gclid: 'EAIaIQ' }), true)
  assert.equal(adVisit({ gclid: 'EAIaIQ', utm_source: 'google ', utm_medium: 'google ' }), true)
  // The tags without the id: only with a paid medium.
  assert.equal(adVisit({ utm_source: 'Google', utm_medium: 'CPC' }), true)
  assert.equal(adVisit({ utm_source: ' bing', utm_medium: 'ppc ' }), true)
  assert.equal(adVisit({ utm_source: 'google', utm_medium: 'organic' }), false)
  assert.equal(adVisit({ utm_source: 'google' }), false)
  // Kayak is Kayak's, whatever else the address carries; anything else is not an ad of these two.
  assert.equal(adVisit({ utm_source: 'kayak', utm_medium: 'cpc', gclid: 'x' }), false)
  assert.equal(adVisit({ utm_source: 'facebook', utm_medium: 'paid_social' }), false)
  assert.equal(adVisit({}), false)
})

test('the hook reads the ad visit on the tab\'s first touch and the page\'s trip only for one', () => {
  assert.match(hook, /function adVisitNow\(\): boolean \{\s+return adVisit\(getUtm\(\)\)\s+\}/)
  assert.match(hook, /const ad = adVisitNow\(\)[\s\S]*?ad,\s+\/\/[^\n]*\n\s+destination: ad && destinationTrip\(\) !== null,/)
})

test('the ads card takes the newest of the page\'s trips that names a destination, for the moment and the words', () => {
  // CodeRabbit #269: `?gclid=…&from=JFK` on the page about Paris — the address names only an origin, and it
  // came first: the moment did not come, and the words would have asked where to.
  const fromAddress = tripFromUrl('?gclid=x&from=JFK')
  const fromTitle = tripFromTitle('Business class flights to Paris')
  assert.equal(hasContext(pickTrip(fromAddress, fromTitle)), false)
  assert.deepEqual(pickTrip(...[fromAddress, fromTitle].filter(hasContext)), fromTitle)
  assert.match(hook, /function destinationTrip\(\): TripPayload \| null \{\s+return pickTrip\(\.\.\.pageTrips\(\)\.filter\(hasContext\)\)\s+\}/)
  assert.match(hook, /const trip = trigger === 'ads' \? destinationTrip\(\) : currentTrip\(\)/)
})

test('a destination code equal to the origin\'s names no place (the server drops it)', () => {
  assert.equal(hasContext({ source: 'search', origin_code: 'CDG', destination_code: 'CDG' }), false)
  assert.equal(hasContext({ source: 'search', origin_code: 'JFK', destination_code: 'CDG' }), true)
  assert.equal(hasContext({ source: 'search', destination_code: 'CDG' }), true)
  // A name is kept by the server whatever the codes say.
  assert.equal(hasContext({ source: 'search', origin_code: 'CDG', destination_code: 'CDG', destination_name: 'Paris' }), true)
  assert.equal(hasContext(tripFromUrl('?gclid=x&from=CDG&to=CDG')), false)
})

test('a moment the server switched off on its own leaves the visit\'s moment to the others', () => {
  // CodeRabbit #269: with PROACTIVE_TRIGGERS=kayak,quote,reader the ads moment came first, used the visit, and
  // the reader never came for an ad visitor on a destination page.
  const adPage = { ad: true, destination: true, visibleS: 30, scrolledScreens: 1 }
  assert.equal(at(adPage), 'ads')
  assert.equal(at({ ...adPage, off: ['ads'] }), 'reader')
  assert.equal(at({ ...adPage, visibleS: 10, off: ['ads'] }), null)
  assert.equal(at({ kayak: true, ...adPage, off: ['kayak'] }), 'ads')
  assert.equal(at({ kayak: true, ...adPage, off: ['kayak', 'ads', 'reader'] }), null)
  // A pressed form whose moment is off: nothing, and nothing else over it.
  assert.equal(at({ ...adPage, off: ['quote'], quote: pressed({ sinceFormActivityS: QUOTE_IDLE_S }) }), null)
  // What the tab keeps: known moments, each once.
  assert.deepEqual(readOff('ads, reader,bogus,,ads'), ['ads', 'reader'])
  assert.equal(offAfter(null, 'ads'), 'ads')
  assert.equal(offAfter('ads', 'ads'), 'ads')
  assert.equal(offAfter('ads,bogus', 'kayak'), 'ads,kayak')
  assert.deepEqual(readOff(null), [])
  assert.equal(TRIGGER_OFF_ARM, 'trigger_off')
  // The hook: the answer "trigger_off" marks the moment off and gives the visit its moment back; any other
  // "no" (the feature off, the control arm, no answer) still ends the visit's moments, as before.
  const ask = hook.slice(hook.indexOf('const ask = async'), hook.indexOf('const evaluate = () => {'))
  assert.match(ask, /if \(res && !res\.show && res\.arm === TRIGGER_OFF_ARM\) \{[\s\S]*?ssSet\(OFF_KEY, offAfter\(ssGet\(OFF_KEY\), trigger\)\)\s+if \(ssGet\(DONE_KEY\) === 'asked'\) ssSet\(DONE_KEY, ''\)\s+fired = false\s+return\s+\}/)
  assert.ok(ask.indexOf('TRIGGER_OFF_ARM') > ask.indexOf("posthog?.capture('proactive_eligible'"), 'measured first')
  assert.match(hook, /off: readOff\(ssGet\(OFF_KEY\)\),/)
})

// ── "In view": the visitor's seconds, from when the page showed (18 Nov: the Kayak card came at ~9 s) ──

test('the time in view before the widget started counts from the first paint', () => {
  // The page showed at 0.9 s, the widget started at 4.2 s: 3.3 s in view already.
  assert.equal(inViewBeforeStartMs(4200, 900, true, false), 3300)
  // Hidden now, hidden since the paint, or no paint known: nothing before the start counts (as before).
  assert.equal(inViewBeforeStartMs(4200, 900, false, false), 0)
  assert.equal(inViewBeforeStartMs(4200, 900, true, true), 0)
  assert.equal(inViewBeforeStartMs(4200, null, true, false), 0)
  assert.equal(inViewBeforeStartMs(NaN, 900, true, false), 0)
  assert.equal(inViewBeforeStartMs(4200, NaN, true, false), 0)
  // A paint "after" now (another clock) never takes time away.
  assert.equal(inViewBeforeStartMs(500, 900, true, false), 0)
  // With it, a Kayak visitor whose widget started 3.3 s late gets the card at the 5th second of the page.
  assert.equal(at({ kayak: true, visibleS: (inViewBeforeStartMs(4200, 900, true, false) + 2000) / 1000, watchedS: 2 }), 'kayak')
})

test('no moment before the widget has watched the page for 2 s, however long the page was in view', () => {
  assert.equal(WATCHED_FIRST_S, 2)
  // The script started 10 s into the visit: due at once, but it first sees whether the visitor types or works
  // on a form (a page without the site's signals says nothing of what came before).
  for (const over of [{ kayak: true }, { ad: true, destination: true }, { scrolledScreens: 2, visibleS: 40 }]) {
    assert.equal(at({ visibleS: 10, ...over, watchedS: WATCHED_FIRST_S - 1 }), null, JSON.stringify(over))
    assert.notEqual(at({ visibleS: 10, ...over, watchedS: WATCHED_FIRST_S }), null, JSON.stringify(over))
  }
  // What it sees in that time holds the moment as always.
  assert.equal(at({ kayak: true, visibleS: 10, watchedS: WATCHED_FIRST_S, sinceSiteTypingS: 0.5 }), null)
  assert.match(hook, /watchedS: \(now - start\) \/ 1000,/)
})

test('the hook counts from the first paint, scrolls from where the page is, and measures its start', () => {
  const run = hook.slice(hook.indexOf('let sentInline = false'), hook.indexOf('const visibleS = () =>'))
  assert.match(run, /const paintMs = firstPaintMs\(\)/)
  assert.match(run, /let visibleAcc = inViewBeforeStartMs\(pageNowMs, paintMs, inView, paintMs !== null && hiddenSince\(paintMs\)\)/)
  assert.match(run, /let visibleSince: number \| null = inView \? start : null/)
  assert.match(run, /let maxScroll = window\.scrollY \|\| document\.documentElement\.scrollTop \|\| 0/)
  // The paint as the browser reports it, and never before a page prepared ahead was shown.
  assert.match(hook, /performance\.getEntriesByName\('first-contentful-paint'\)\[0\]/)
  assert.match(hook, /return Math\.max\(paint\.startTime, nav\?\.activationStart \?\? 0\)/)
  assert.match(hook, /performance\.getEntriesByType\('visibility-state'\)\.some\(\(e\) => e\.name === 'hidden' && e\.startTime >= fromMs\)/)
  const ask = hook.slice(hook.indexOf('const ask = async'), hook.indexOf('const evaluate = () => {'))
  assert.match(ask, /\.\.\.\(widgetStartS !== null \? \{ widget_start_s: widgetStartS \} : \{\}\)/)
})

test('a quote form being worked on holds kayak and reader, pressed or not (67 of 229 Kayak cards came over one)', () => {
  // Opened and touched 5 s ago, never pressed: no card over it, however long the visit.
  assert.equal(at({ kayak: true, visibleS: 30, sinceQuoteFormS: 5 }), null)
  assert.equal(at({ visibleS: 60, scrolledScreens: 3, sinceQuoteFormS: 40 }), null)
  // The typing pause alone used to let the card through: 6 s after the last key, still nothing.
  assert.equal(at({ kayak: true, visibleS: 30, sinceSiteTypingS: 6, sinceQuoteFormS: 6 }), null)
  // Untouched for longer than the watch: the form is part of the page again.
  assert.equal(at({ kayak: true, visibleS: 300, sinceQuoteFormS: QUOTE_WATCH_S + 1 }), 'kayak')
  // A press still brings the quote moment once the form goes quiet, as before.
  assert.equal(at({ kayak: true, visibleS: 30, sinceQuoteFormS: QUOTE_IDLE_S, quote: pressed({ sinceFormActivityS: QUOTE_IDLE_S }) }), 'quote')
  // Back in the form long after an old press: held again.
  assert.equal(at({ kayak: true, visibleS: 300, sinceQuoteFormS: 1, quote: pressed({ sincePressS: 200, sinceFormActivityS: 1 }) }), null)
})

test('the hook marks the quote form worked on from the page and from the site, pressed or not', () => {
  assert.match(hook, /const touchQuoteForm = \(t: EventTarget \| null\) => \{\s+if \(t instanceof Element && !inWidget\(t\) && quoteForm\(t\)\) quoteFormAt = Date\.now\(\)/)
  // Typing, focus/change and clicks inside the form all count.
  assert.match(hook, /const onType = [\s\S]*?touchQuoteForm\(e\.target\)/)
  assert.match(hook, /const onFormTouch = \(e: Event\) => \{\s+touchQuoteForm\(e\.target\)/)
  assert.match(hook, /if \(!el \|\| inWidget\(el\)\) return\s+touchQuoteForm\(el\)/)
  // The site's word: opened, a step, a refused or failed send, a send in flight (siteSignals QUOTE_FORM_WORK,
  // the list itself in site-signals.test.mjs).
  assert.match(hook, /if \(isQuoteFormWork\(e\)\) quoteFormAt = Math\.max\(quoteFormAt \?\? 0, Math\.min\(e\.at, Date\.now\(\)\)\)/)
  assert.match(hook, /sinceQuoteFormS: quoteFormAt === null \? null : \(now - quoteFormAt\) \/ 1000/)
  // CodeRabbit #267: work on the form while the server's answer is on its way drops a kayak or reader
  // card; the quote moment keeps its press check.
  assert.match(hook, /const seen = \(\) => \(\{[^}]*quoteForm: quoteFormAt,/)
  assert.match(hook, /if \(trigger !== 'quote' && quoteFormAt !== before\.quoteForm\) return 'quote_form'/)
})

test('the 10 s note waits while the quote form is worked on, as the moments do (owner, 17 Nov)', () => {
  // Never touched, or untouched for longer than the watch: no wait.
  assert.equal(noteWaitMs(null), 0)
  assert.equal(noteWaitMs(QUOTE_WATCH_S + 0.001), 0)
  assert.equal(noteWaitMs(QUOTE_WATCH_S + 60), 0)
  // Touched: it waits until just past QUOTE_WATCH_S since that touch, never 0 while held (no busy loop).
  assert.equal(noteWaitMs(0), QUOTE_WATCH_S * 1000 + 1)
  assert.equal(noteWaitMs(12), (QUOTE_WATCH_S - 12) * 1000 + 1)
  assert.equal(noteWaitMs(QUOTE_WATCH_S), 1)
  for (const s of [0.0004, 179.9999, QUOTE_WATCH_S - 1e-9]) assert.ok(noteWaitMs(s) >= 1, String(s))
  // A touch stamped ahead of this clock counts as now, not as a longer wait.
  assert.equal(noteWaitMs(-5), QUOTE_WATCH_S * 1000 + 1)
})

test('the note asks again when the form is part of the page again, and one on screen goes when the form starts', () => {
  const note = widget.slice(widget.indexOf('// ─── The greeting beside the button'), widget.indexOf('// Keep an open chat open'))
  assert.match(note, /const wait = noteWaitMs\(formAt === null \? null : \(Date\.now\(\) - formAt\) \/ 1000\)\s+if \(wait > 0\) \{/)
  assert.match(note, /clearTimeout\(attentionTimer\)\s+attentionTimer = setTimeout\(show, wait\)\s+return\s+\}\s+autoOpenedRef\.current = true/)
  assert.match(note, /const stopFormWatch = watchQuoteForm\(\(at\) => \{\s+formAt = Math\.max\(formAt \?\? 0, at\)\s+setShowAttention\(false\)\s+\}\)/)
  assert.match(note, /attentionTimer = setTimeout\(show, 10_000\)/)
  // The early note on a computer (pointer leaving through the top) goes through the same wait.
  assert.match(note, /Date\.now\(\) - loadedAt < 3000\) return\s+show\(\)/)
  assert.match(note, /return \(\) => \{\s+clearTimeout\(attentionTimer\)\s+stopFormWatch\(\)/)
  // The watch: what the hook counts as work on the form, from the page (never the widget) and from the site.
  assert.match(hook, /export function watchQuoteForm\(onWork: \(at: number\) => void\): \(\) => void \{/)
  assert.match(hook, /if \(t instanceof Element && !t\.closest\('#bbc-widget-root'\) && quoteForm\(t\)\) onWork\(Date\.now\(\)\)/)
  assert.match(hook, /if \(isQuoteFormWork\(e\)\) onWork\(Math\.min\(e\.at, Date\.now\(\)\)\)/)
  assert.match(hook, /const types = \['keydown', 'input', 'change', 'focusin', 'click'\] as const/)
  assert.match(hook, /for \(const e of siteEvents\(\)\) onSite\(e\)\s+const offSite = onSiteEvent\(onSite\)/)
  assert.match(hook, /for \(const type of types\) document\.removeEventListener\(type, onPage, cap\)\s+offSite\(\)/)
  // The same five page events the hook reads the form's touches from.
  for (const type of ['keydown', 'input', 'change', 'focusin', 'click']) {
    assert.match(hook, new RegExp(`document\\.addEventListener\\('${type}', on(Type|FormTouch|Click), cap\\)`), type)
  }
})

test('the quote card hears which fields the form refused, by kind only (owner, 18 Nov)', () => {
  // The browser's refusal: the field's type, then its autocomplete, name or id, never its value.
  assert.match(hook, /function refusedKind\(t: EventTarget \| null\): FieldKind \| null \{/)
  assert.match(hook, /if \(t instanceof HTMLInputElement && t\.type === 'email'\) return 'email'/)
  assert.match(hook, /if \(t instanceof HTMLInputElement && t\.type === 'tel'\) return 'phone'/)
  assert.match(hook, /return kindOfField\(t\.getAttribute\('autocomplete'\), t\.name, t\.id\)/)
  assert.doesNotMatch(hook.slice(hook.indexOf('function refusedKind'), hook.indexOf('function isSubmitControl')), /\.value/)
  // Each press starts a new attempt; each refused field of it is added once, six at most.
  assert.equal([...hook.matchAll(/press = \{ at: Date\.now\(\), form(?:: null)?, activityAt: Date\.now\(\), invalid: false \}\s+refused = \[\]/g)].length, 2)
  assert.match(hook, /refused = withKinds\(refused, \[refusedKind\(e\.target\)\]\)/)
  // A submit is a send of its own too (Enter in a field with no recognized click).
  assert.match(hook, /press = \{ at: Date\.now\(\), form, activityAt: Date\.now\(\), invalid: press\?\.invalid \?\? false \}\s+refused = \[\]/)
  // The site's word: its first one for our press, right after it, joins the browser's kinds of that send (a
  // word without fields takes nothing away); any other is a send of its own, and its kinds start a new list
  // (CodeRabbit #268, round 3: email refused, then dates refused, is a dates-only refusal).
  assert.match(hook, /const sameSend = !!press && siteSaidFor !== press\.at && e\.at - press\.at <= SAME_SEND_MS/)
  assert.match(hook, /refused = sameSend \? withKinds\(refused, kinds\) : kinds/)
  assert.match(hook, /if \(siteRefused\) siteSaidFor = press\?\.at \?\? null/)
  // The owner is recorded after the press is updated, so a site word that started the press owns it.
  assert.ok(hook.indexOf('if (siteRefused) siteSaidFor') > hook.indexOf('press = fx.press'))
  assert.equal(SAME_SEND_MS, 5000)
  // Only the quote moment sends them, and only when there are some.
  assert.match(hook, /\.\.\.\(trigger === 'quote' && refused\.length \? \{ refused: \[\.\.\.refused\] \} : \{\}\)/)
  assert.match(api, /body: \{ visitor_id: string; trigger: string; trip: unknown; site: string; refused\?: string\[\] \}/)
  // The card's words come back with their variant, and the card's shown event carries it (as engaged/dismissed do).
  const cardShown = payloads('proactive_shown').filter((p) => !p.includes("trigger: 'note'"))
  assert.equal(cardShown.length, 1)
  assert.ok(cardShown[0].includes('...(res.variant ? { variant: res.variant } : {})'))
})

// The payload of each capture call of `event`, up to the line that closes it: an assertion on one event can
// never be satisfied by the next event's payload (CodeRabbit #267).
const payloads = (event) => [...hook.matchAll(new RegExp(`capture\\('${event}', \\{([\\s\\S]*?)\\n\\s*\\}\\)`, 'g'))].map((m) => m[1])

test('the note\'s variant goes out with the answer and with its measurements, and nothing else passes for one', () => {
  assert.equal(offerVariant('places'), 'places')
  assert.equal(offerVariant('open'), 'open')
  for (const bad of [undefined, null, 3, '', 'Places', 'pl aces', '<b>', 'x'.repeat(30), ['open']]) {
    assert.equal(offerVariant(bad), undefined, String(bad))
  }
  assert.match(api, /const variant = offerVariant\(data\.variant\)/)
  assert.match(hook, /trigger: 'note', message: res\.message[^}]*shownAt: Date\.now\(\),\s*\.\.\.\(res\.variant \? \{ variant: res\.variant \} : \{\}\),\s*\}/)
  assert.match(chat, /proactive: \{ trigger: opening\.trigger, \.\.\.\(opening\.variant \? \{ variant: opening\.variant \} : \{\}\) \}/)
  for (const ev of ['proactive_dismissed', 'proactive_engaged']) {
    const all = payloads(ev)
    assert.equal(all.length, 1, ev)
    assert.ok(all[0].includes('...(o.variant ? { variant: o.variant } : {})'), ev)
  }
  const noteShown = payloads('proactive_shown').filter((p) => p.includes("trigger: 'note'"))
  assert.equal(noteShown.length, 1)
  assert.ok(noteShown[0].includes('...(res.variant ? { variant: res.variant } : {})'))
})

test('never on the thank-you or legal pages, never for an idle visitor, never mid-typing', () => {
  for (const path of ['/thank-you', '/thank-you/', '/legal', '/terms-and-conditions', '/privacy', '/thanks']) {
    assert.equal(blockedPath(path), true, path)
    assert.equal(at({ path, kayak: true, visibleS: 30 }), null, path)
  }
  assert.equal(blockedPath('/business-class-flights'), false)
  assert.equal(blockedPath('/flights/terminal-guide'), false)
  assert.equal(at({ kayak: true, visibleS: 30, sinceActivityS: 61 }), null)
  assert.equal(at({ kayak: true, visibleS: 30, sinceSiteTypingS: 2 }), null)
  assert.equal(at({ kayak: true, visibleS: 30, sinceSiteTypingS: 6 }), 'kayak')
})

test('the 24 h pause, "Not now" and what a reply was', () => {
  assert.equal(pauseRunning(String(2_000), 1_000), true)
  assert.equal(pauseRunning(String(1_000), 2_000), false)
  assert.equal(pauseRunning(null, 1_000), false)
  assert.equal(pauseRunning('soon', 1_000), false)
  assert.equal(isNotNow(' Not now '), true)
  assert.equal(isNotNow('Not now, thanks'), false)
  const replies = ['Yes, send me fares', 'Change details', 'Not now']
  assert.equal(replyKind('Yes, send me fares', replies), 'button_1')
  assert.equal(replyKind('change details', replies), 'button_2')
  assert.equal(replyKind('my email is …', replies), 'text')
})

// ── The wiring (source pins) ─────────────────────────────────────────────────────────

test('the card-opened chat makes no /chat/start: the first message creates the conversation', () => {
  const mount = chat.slice(chat.indexOf('// AI opens on fresh sessions'))
  assert.ok(mount.indexOf('if (openingRef.current) {') < mount.indexOf('startConversation()'))
  assert.match(chat, /\.\.\.\(opening \? \{\s+trip: opening\.trip,\s+proactive: \{ trigger: opening\.trigger,/)
})

test('the stored opening moves the poll cursor before the stream connects', () => {
  const init = chat.slice(chat.indexOf('const newConvId = initData.conversation_id as string'))
  const cursor = init.indexOf('lastMsgTime.current = initData.opening_at')
  const connect = init.indexOf('setConvId(newConvId)')
  assert.ok(cursor > 0 && cursor < connect, 'opening_at must be the cursor before setConvId')
  assert.match(init, /markProactiveConversation\(newConvId\)/)
})

test('"Not now" never reaches the server; ✕ before a conversation is a dismissal', () => {
  const quick = chat.slice(chat.indexOf('const handleQuickReply'), chat.indexOf('const handleKeyDown'))
  assert.ok(quick.indexOf('isNotNow(text)') < quick.indexOf('sendMessage(text)'))
  assert.match(quick, /onProactiveDismiss\?\.\('not_now'\)\s+onClose\(\)\s+return/)
  assert.match(chat, /if \(openingRef\.current && !convId\) onProactiveDismiss\?\.\('close'\)/)
})

test('the conversation carries the PostHog session for the replay link', () => {
  assert.equal((chat.match(/metadata: withSession\(metadata\)/g) || []).length, 2)
})

test('the offer has its 3.5 s answer or nothing, and says how it went', () => {
  // 18 Nov: 84 of 990 moments got no answer in 1.5 s from a server that answers in under 0.2 s.
  assert.equal(OFFER_TIMEOUT_MS, 3500)
  assert.match(api, /\/api\/chat\/proactive\/offer/)
  assert.match(api, /ctrl\?\.abort\(\)/)
  assert.match(hook, /fetchProactiveOffer\(apiUrl, \{\s+visitor_id: ensureVisitorId\(\), trigger, trip, site: brand\.id,\s+\.\.\.\(trigger === 'quote' && refused\.length \? \{ refused: \[\.\.\.refused\] \} : \{\}\),\s+\}, OFFER_TIMEOUT_MS, \(t\) => took\.push\(t\)\)/)
  const ask = hook.slice(hook.indexOf('const ask = async'), hook.indexOf('const evaluate = () => {'))
  assert.match(ask, /\.\.\.offerMeasures\(took\[0\]\),/)
})

test('offer measurements: the time to the nearest 10 ms, and why no answer came', () => {
  assert.deepEqual(offerMeasures({ ms: 1234, fail: null }), { offer_ms: 1230 })
  assert.deepEqual(offerMeasures({ ms: 3504, fail: 'timeout' }), { offer_ms: 3500, offer_fail: 'timeout' })
  assert.deepEqual(offerMeasures({ ms: -5, fail: 'network' }), { offer_ms: 0, offer_fail: 'network' })
  assert.deepEqual(offerMeasures(undefined), {})
  assert.deepEqual(offerMeasures({ ms: NaN, fail: 'http' }), {})
})

test('an offer request reports how it went, once, whatever the outcome', async () => {
  const realFetch = globalThis.fetch
  const body = { visitor_id: 'v-12345678', trigger: 'kayak', trip: null, site: 'bbc' }
  const offer = { show: true, arm: 'proactive', message: 'Hi', quick_replies: ['Not now'] }
  const run = async (fetchImpl, timeoutMs = 200) => {
    globalThis.fetch = fetchImpl
    const seen = []
    const res = await fetchProactiveOffer('http://api.test', body, timeoutMs, (t) => seen.push(t))
    assert.equal(seen.length, 1)
    return { res, ...seen[0] }
  }
  const reply = (status, data) => async () => new Response(JSON.stringify(data), { status })
  try {
    let r = await run(reply(200, offer))
    assert.equal(r.fail, null)
    assert.equal(r.res.message, 'Hi')
    assert.ok(r.ms >= 0)
    r = await run(reply(500, { detail: 'x' }))
    assert.deepEqual([r.res, r.fail], [null, 'http'])
    r = await run(reply(200, { nope: 1 }))
    assert.deepEqual([r.res, r.fail], [null, 'shape'])
    // A reply that is not JSON at all (a proxy's HTML page) is the reply's fault, not the line's.
    r = await run(async () => new Response('<html>busy</html>', { status: 200 }))
    assert.deepEqual([r.res, r.fail], [null, 'shape'])
    r = await run(async () => { throw new TypeError('Failed to fetch') })
    assert.deepEqual([r.res, r.fail], [null, 'network'])
    // The answer takes longer than the wait: our own time ran out, and the request was called off.
    let aborted = false
    r = await run((url, init) => new Promise((resolve, reject) => {
      const t = setTimeout(() => resolve(new Response(JSON.stringify(offer))), 5000)
      init.signal.addEventListener('abort', () => {
        aborted = true
        clearTimeout(t)
        reject(new DOMException('aborted', 'AbortError'))
      })
    }), 50)
    assert.deepEqual([r.res, r.fail, aborted], [null, 'timeout', true])
    assert.ok(r.ms >= 40, `${r.ms} ms`)
    // A measurement that throws never costs the answer.
    globalThis.fetch = reply(200, offer)
    const res = await fetchProactiveOffer('http://api.test', body, 200, () => { throw new Error('boom') })
    assert.equal(res.message, 'Hi')
  } finally {
    globalThis.fetch = realFetch
  }
})

test('the connection to the API opens when the widget starts, once, as the anonymous one fetch() uses', () => {
  const realDocument = globalThis.document
  const added = []
  globalThis.document = {
    querySelectorAll: () => added,
    createElement: () => ({}),
    head: { appendChild: (el) => { el.href = `${el.href}/`; added.push(el) } },
  }
  try {
    preconnect('https://admin-panel-error-production.up.railway.app')
    preconnect('https://admin-panel-error-production.up.railway.app/')
    assert.equal(added.length, 1)
    assert.equal(added[0].rel, 'preconnect')
    assert.equal(added[0].crossOrigin, 'anonymous')
    assert.equal(added[0].href, 'https://admin-panel-error-production.up.railway.app/')
    // Not an address: nothing, and nothing thrown.
    preconnect('not a url')
    preconnect('javascript:alert(1)')
    assert.equal(added.length, 1)
  } finally {
    globalThis.document = realDocument
  }
  const entry = read('entry.tsx')
  assert.match(entry, /\|\| 'https:\/\/admin-panel-error-production\.up\.railway\.app'\n[\s\S]{0,200}preconnect\(apiUrl\)/)
})

test('the guards: one moment per visit, the 24 h pause, the closed note, the CRM iframe', () => {
  assert.match(hook, /if \(ssGet\(DONE_KEY\) \|\| ssGet\(NOTE_CLOSED_KEY\) \|\| pauseRunning\(lsGet\(PAUSE_KEY\), Date\.now\(\)\)\) return/)
  assert.match(hook, /if \(!allowed \|\| step !== 'buttons'\) return/)
  assert.match(widget, /useProactive\(\{ apiUrl, step, allowed: !embedded && !hasOptimisticSession, restored: keptOpening \}\)/)
  assert.match(buttons, /onClick=\{\(\) => \{ markNoteClosed\(\); onDismissAttention\?\.\(\) \}\}/)
  // The card replaces the note for the rest of the visit.
  assert.match(widget, /showAttention=\{showAttention && !sessionActive && !proactive\.quiet\}/)
})

test('a conversation the card opened is the visitor\'s session: the button restores it, no form', () => {
  assert.match(widget, /const sessionActive = !!\(hasOptimisticSession && \(savedVisitor \|\| proactiveConv\)\)/)
  assert.match(widget, /if \(hasOptimisticSession && \(savedVisitor \|\| proactiveConv\)\) \{/)
})

test('the corner button next to a card opens the card\'s chat, not the form', () => {
  assert.match(widget, /onSelect=\{\(t\) => \(proactive\.offer \? openProactive\(proactive\.offer\) : openFromButton\(t\)\)\}/)
})

test('the first send owns the opening: a racing second send goes out plain', () => {
  const send = chat.slice(chat.indexOf('let activeConvId = convId'))
  assert.match(send, /const opening = convId \? null : openingRef\.current\s+if \(opening\) openingRef\.current = null/)
  // Not created: the next send carries it again.
  assert.match(send, /\} else if \(opening\) \{\s+openingRef\.current = opening/)
  assert.match(send, /\} catch \(e\) \{\s+if \(opening\) openingRef\.current = opening/)
})

test('a phone gets the card, a computer the panel', () => {
  assert.match(hook, /const surface: Surface = phone \? 'card' : 'panel'/)
  assert.match(widget, /if \(proactive\.offer\?\.surface === 'panel'\) openProactive\(proactive\.offer\)/)
  assert.match(widget, /card=\{proactive\.offer\?\.surface === 'card' \? proactive\.offer : null\}/)
})

// ── CodeRabbit round on #256 ─────────────────────────────────────────────────────────

test('the card\'s chat never falls back to a plain conversation: the send fails, Retry carries the opening', () => {
  const send = chat.slice(chat.indexOf('let activeConvId = convId'), chat.indexOf("const res = await apiFetch(`${apiUrl}/api/chat`, {"))
  assert.match(send, /if \(opening && !activeConvId\) throw new Error\('init failed'\)/)
})

test('the note closed with ✕ stops the moments on the next tick, not only on the next page', () => {
  const tick = hook.slice(hook.indexOf('const evaluate = () => {'), hook.indexOf('const quote: QuoteState | null'))
  assert.match(tick, /if \(ssGet\(NOTE_CLOSED_KEY\)\) return/)
})

test('an answer that comes after the visitor moved on is dropped and measured, never shown', () => {
  const ask = hook.slice(hook.indexOf('const ask = async'), hook.indexOf('const evaluate = () => {'))
  assert.ok(ask.indexOf('const before = seen()') < ask.indexOf('await fetchProactiveOffer('))
  assert.match(ask, /\.\.\.\(passed \? \{ moment_passed: passed \} : \{\}\)/)
  assert.match(ask, /if \(disposed \|\| !res \|\| !res\.show \|\| !res\.message \|\| passed\) return/)
  const why = hook.slice(hook.indexOf('const momentPassed'), hook.indexOf('const ask = async'))
  for (const reason of ['chat_opened', 'hidden', 'note_closed', 'left_page', 'typing', 'quote_form', 'sent']) {
    assert.match(why, new RegExp(`return '${reason}'`), reason)
  }
})

test('a send in flight holds the quote moment for as long as the form says so, not for a set time', () => {
  assert.doesNotMatch(hook, /SEND_IN_FLIGHT_MS|sincePressMs/)
  // The form's own state, or the site's word that a send is in flight (bbc-signals: quote_form_submitting).
  assert.match(hook, /busy: sending\(press\.form\) \|\| siteBusy,/)
  assert.match(hook, /return !!btn && btn\.disabled && !form\.querySelector\(':invalid, \[aria-invalid="true"\]'\)/)
})

test('Escape on the card\'s unanswered chat is a dismissal; on a conversation it only closes', () => {
  const esc = widget.slice(widget.indexOf("if (e.key === 'Escape' && step !== 'buttons') {"))
  const dismiss = esc.indexOf("if (opening && !proactiveChatStarted()) proactive.dismiss('close')")
  assert.ok(dismiss > 0 && dismiss < esc.indexOf('animateClose('))
  assert.match(hook, /export function proactiveChatStarted\(\): boolean \{ return isProactiveConversation\(lsGet\('bbc_conv_id'\)\) \}/)
})

// ── CodeRabbit round 2 on #256 ───────────────────────────────────────────────────────

test('the open panel keeps its unanswered opening across the tab\'s pages: never a chat with nothing behind it', () => {
  // Kept while the visitor has not answered; read back only where no conversation exists yet.
  const keep = widget.slice(widget.indexOf("// Keep an open chat open across this tab's pages"))
  assert.match(keep, /const kept = opening && !proactiveChatStarted\(\) \? \{ opening: keepOpening\(opening\) \} : \{\}/)
  assert.match(keep, /safeSet\('bbc_widget', JSON\.stringify\(\{ step: 'chat', tunnel, visitor, metadata, \.\.\.kept \}\)\)/)
  assert.match(keep, /\}, \[step, tunnel, opening\]\)/)
  assert.match(widget, /const keptOpening = cachedConvId \? null : readKeptOpening\(restored\?\.opening\)/)
  assert.match(widget, /const \[opening, setOpening\] = useState<ProactiveOpening \| null>\(keptOpening\)/)
  // Its answer or dismissal on the next page is still measured against the moment it was shown.
  assert.match(hook, /const shownRef = useRef<ProactiveOpening \| null>\(restored\)/)
  // With the opening, ChatWindow shows its words and waits: no /chat/start.
  assert.match(chat, /if \(openingRef\.current\) \{[\s\S]*?return\s+\}\s+if \(savedConvId\) return\s+startConversation\(\)/)
})

test('a kept opening reads back whole, without the reply already sent; anything else is no opening', () => {
  const o = {
    trigger: 'kayak', message: 'Flying Atlanta → Seoul?', quickReplies: ['Yes, send me fares', 'Not now'],
    trip: { source: 'kayak', origin_code: 'ATL', destination_code: 'ICN' }, surface: 'panel', shownAt: 1763250000000,
    firstReply: 'Yes, send me fares',
  }
  const kept = keepOpening(o)
  assert.equal('firstReply' in kept, false)
  assert.deepEqual(readKeptOpening(JSON.parse(JSON.stringify(kept))), kept)
  for (const bad of [null, 'x', 5, {}, { ...kept, trigger: 'other' }, { ...kept, message: ' ' }, { ...kept, surface: 'sheet' }]) {
    assert.equal(readKeptOpening(bad), null)
  }
  const odd = readKeptOpening({ ...kept, quickReplies: ['ok', 3, ' '], trip: 'ATL-ICN', shownAt: 'soon' })
  assert.deepEqual(odd.quickReplies, ['ok'])
  assert.equal(odd.trip, null)
  assert.equal(typeof odd.shownAt, 'number')
  // Every moment's opening reads back, the ads card's too.
  for (const trigger of ['kayak', 'ads', 'quote', 'reader', 'exit', 'note']) {
    assert.equal(readKeptOpening({ ...kept, trigger })?.trigger, trigger, trigger)
  }
  // The note's variant travels with it to the next page; a value that is not one never does.
  const note = keepOpening({ ...o, trigger: 'note', variant: 'places' })
  assert.equal(readKeptOpening(JSON.parse(JSON.stringify(note))).variant, 'places')
  assert.equal('variant' in readKeptOpening({ ...note, variant: '<script>' }), false)
})

test('the quote form\'s send is read off its submit button by the press\'s own rule (a <button> without a type)', () => {
  assert.match(hook, /function submitControl\(form: HTMLFormElement\)/)
  assert.match(hook, /return Array\.from\(controls\)\.find\(isSubmitControl\) \?\? null/)
  assert.match(hook, /const btn = submitControl\(form\)/)
  assert.doesNotMatch(hook, /querySelector<HTMLButtonElement \| HTMLInputElement>\('button\[type="submit"\], input\[type="submit"\]'\)/)
})

// ── Alex and the look (the 16 Nov debate: ADR 12, 16, 17, 18) ───────────────────────

/** WCAG contrast of two #RRGGBB colours. */
function contrast(a, b) {
  const lum = (hex) => {
    const [r, g, bl] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
      .map(c => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl
  }
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

/** Hue in degrees of a #RRGGBB colour. */
function hue(hex) {
  const [r, g, b] = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
  const max = Math.max(r, g, b), min = Math.min(r, g, b), d = max - min
  if (!d) return 0
  const h = max === r ? ((g - b) / d) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4
  return (h * 60 + 360) % 360
}

test('Alex is a "Travel Agent" on both sites, "AI" read to screen readers; the server uses the same name', () => {
  for (const site of [bbc, bct]) {
    assert.equal(site.persona.name, 'Alex')
    assert.equal(site.persona.initial, 'A')
    // Capital T and A, and no "AI" in the line itself (owner, 16 Nov); the badge that showed it on his photo
    // is gone (owner, 17 Nov), the word read to screen readers stays.
    assert.equal(site.persona.role, 'Travel Agent')
  }
  // The header: the name over "Travel Agent · <brand>", read as "AI Travel Agent · <brand>".
  assert.match(top, /export const personaLine = `\$\{brand\.persona\.role\} · \$\{brand\.name\}`/)
  assert.match(top, /export function PersonaLine\(\) \{\s*return <><span class="bw-sr">AI <\/span>\{personaLine\}<\/>/)
  // A person who writes in the chat takes Alex's place in the header (owner, 17 Nov: name, photo and
  // their own line, tests/operator.test.mjs); until then, and once it is his again, it is Alex's.
  assert.match(top, /<div class="bw-head__name" id=\{titleId\}>\{operator \? operator\.name : brand\.persona\.name\}<\/div>/)
  assert.match(top, /<div class="bw-head__sub">\{operator \? operatorLine : <PersonaLine \/>\}<\/div>/)
  assert.match(chat, /const operator = currentOperator\(shown\)/)
  assert.doesNotMatch(chat, /personaLine/)
  // The typing note read to screen readers says it too: "Alex, the AI Travel Agent, is typing".
  assert.match(chat, /`\$\{brand\.persona\.name\}, the AI \$\{brand\.persona\.role\}, is typing`/)
  // No bubble is signed "Assistant" any more, the unread preview included.
  assert.doesNotMatch(chat, /'Assistant'|<b>Assistant<\/b>/)
  // The server's openings: "Hi, I'm Alex from …" (bbc-chatbot-api/app/services/proactive.py, PERSONA).
  assert.match(read('config/types.ts'), /PERSONA\): keep them equal/)
})

test('Alex has his photo in the header, beside his messages and beside the button; no "AI" badge on it', () => {
  // A colleague's photo, with his permission (owner, 16 Nov). Part of the bundle, not a link: small,
  // and no request can fail on its way to it.
  assert.match(ALEX_PHOTO, /^data:image\/jpeg;base64,[A-Za-z0-9+/]+=*$/)
  assert.ok(ALEX_PHOTO.length < 16000, `${ALEX_PHOTO.length} characters`)
  // One component in all three spots.
  assert.match(top, /<PersonaAvatar cls="bw-head__ava">\s*<i class="bw-head__live" \/>\s*<\/PersonaAvatar>/)
  assert.match(chat, /const brandAvatar = <PersonaAvatar cls="bw-ava bw-ava--persona" \/>/)
  assert.match(buttons, /const personaAvatar = <PersonaAvatar cls="bw-nudge__ava bw-nudge__ava--persona" \/>/)
  assert.match(buttons, /<span class="bw-nudge__who">\{personaAvatar\}\{personaWho\}<\/span>/)
  // A page that refuses embedded images gets the letter on the brand colour, as before.
  const avatar = read('PersonaAvatar.tsx')
  assert.match(avatar, /<img src=\{ALEX_PHOTO\} alt="" draggable=\{false\} onError=\{\(\) => setRefused\(true\)\} \/>/)
  assert.match(avatar, /\? brand\.persona\.initial/)
  assert.match(avatar, /aria-hidden="true"/)
  assert.match(WIDGET_CSS, /\.bw-photo > img \{ display: block; width: 100%; height: 100%; border-radius: 50%; object-fit: cover; \}/)
  // No "AI" badge on his photo, in any spot (owner, 17 Nov: removed from the header, the last place it showed).
  assert.doesNotMatch(avatar, /badge\s*[=?:]|badge &&|bw-ai/)
  assert.doesNotMatch(top + chat + buttons, /<PersonaAvatar [^>]*\bbadge\b/)
  assert.doesNotMatch(WIDGET_CSS, /\.bw-ai\b/)
  // The green dot keeps the header avatar's bottom right.
  assert.match(WIDGET_CSS, /\.bw-head__ava \{\s*position: relative;/)
  assert.match(WIDGET_CSS, /\.bw-head__live \{\s*position: absolute; right: -1px; bottom: -1px;/)
  // Beside the face, the button's card has the header's line: "Travel Agent · <brand>", "AI" read first.
  assert.match(buttons, /const personaWho = \(\s*<span class="bw-nudge__id"><b>\{brand\.persona\.name\}<\/b><small><PersonaLine \/><\/small><\/span>/)
  // The logo stays only on the corner button, which the site draws a stand-in of before this script loads.
  assert.equal(buttons.match(/brand\.logoUrl/g)?.length, 1)
  assert.match(buttons, /<img src=\{brand\.logoUrl\} alt="" width=\{30\} height=\{30\} draggable=\{false\} \/>/)
})

test('BBC is blue and BCT burgundy, and every word on them stays readable', () => {
  assert.ok(hue(bbc.look.primary) >= 200 && hue(bbc.look.primary) <= 250, `BBC ${bbc.look.primary}`)
  const bctHue = hue(bct.look.primary)
  assert.ok(bctHue >= 330 || bctHue <= 10, `BCT ${bct.look.primary}`)
  for (const { look } of [bbc, bct]) {
    assert.ok(contrast('#FFFFFF', look.primary) >= 4.5, `white on ${look.primary}`)   // buttons, the visitor's bubbles
    assert.ok(contrast(look.primary, '#FFFFFF') >= 4.5, `${look.primary} on white`)   // the reply pills
    assert.ok(contrast(look.deep, look.soft) >= 7, `${look.deep} on ${look.soft}`)
    assert.ok(contrast(look.quiet, look.soft) >= 4.5, `${look.quiet} on ${look.soft}`)
    assert.equal(look.blobs.length, 3)
    assert.equal(look.primaryRgb, [1, 3, 5].map(i => parseInt(look.primary.slice(i, i + 2), 16)).join(','))
  }
  // The corner button keeps the site's stand-in: changing it is a change on the site too.
  assert.equal(bbc.colors.launcher, '#0B1829')
  assert.equal(bbc.colors.launcherRing, '#C9A54E')
  assert.equal(bct.colors.launcherRing, '#800020')
  for (const v of ['--bw-primary', '--bw-primary-rgb', '--bw-primary-hover', '--bw-deep', '--bw-quiet', '--bw-soft', '--bw-blob-1', '--bw-blob-2', '--bw-blob-3']) {
    assert.match(read('entry.tsx'), new RegExp(`s\\.setProperty\\('${v}', `))
  }
})

test('few messages sit by the buttons and the composer, never under an empty band', () => {
  // A flexible first item, not justify-content: flex-end, so a long chat still scrolls to its top.
  assert.match(WIDGET_CSS, /#bbc-widget-root \.bw-log::before \{ content: ''; flex: 1 1 auto; \}/)
  assert.doesNotMatch(WIDGET_CSS, /\.bw-log \{[^}]*justify-content: flex-end/)
  // The canvas's shapes: pill replies, a rounded composer with a round send button, rounded bubbles.
  assert.match(WIDGET_CSS, /\.bw-chip \{[^}]*border-radius: 999px/)
  assert.match(WIDGET_CSS, /\.bw-compose \{[^}]*border-radius: 24px/)
  assert.match(WIDGET_CSS, /\.bw-send \{[^}]*border-radius: 50%/)
  assert.match(WIDGET_CSS, /\.bw-row--out \.bw-msg \{[^}]*border-radius: 18px 18px 6px 18px/)
  // Touch targets: the pills reach 44 px on a phone.
  assert.match(WIDGET_CSS, /@media \(max-width: 767px\) \{[\s\S]*\.bw-chip \{ min-height: 44px/)
})

test('the site goes with the offer and with every proactive event, so BBC and BCT are never mixed', () => {
  const captures = hook.match(/posthog\?\.capture\('proactive_[a-z_]+', \{[\s\S]*?\}\)/g) || []
  // The five of the moments, and the note's proactive_shown and proactive_note_unanswered.
  assert.equal(captures.length, 7)
  for (const call of captures) assert.match(call, /site: brand\.id/, call.slice(0, 60))
  assert.match(api, /body: \{ visitor_id: string; trigger: string; trip: unknown; site: string; refused\?: string\[\] \}/)
})

test('a message waiting to go reads at full strength, not faded white on the brand colour', () => {
  // CodeRabbit: opacity 0.62 put white text on blue at about 3:1, below the 4.5:1 normal text needs.
  const rule = WIDGET_CSS.slice(WIDGET_CSS.indexOf('.bw-row--queued .bw-msg {'))
  const block = rule.slice(0, rule.indexOf('}'))
  assert.doesNotMatch(block, /opacity/)
  assert.match(block, /color: var\(--bw-text\)/)
  assert.match(block, /outline: 1\.5px dashed/)
})

// ── The note the visitor taps: the chat with Alex, no form (owner's decision, 16 Nov) ──────────────

test('a tap on the 10 s note opens the chat with no form; without an answer in time, the form as before', () => {
  // The note's own button (the corner button takes the same door: the next test).
  assert.match(buttons, /class="bw-nudge__open" onClick=\{\(\) => \(onNoteOpen \? onNoteOpen\(\) : onSelect\('sales'\)\)\}/)
  assert.match(buttons, /class="bw-launch"\s+onClick=\{\(\) => onSelect\('sales'\)\}/)
  assert.match(widget, /onNoteOpen=\{\(\) => \{ if \(proactive\.offer\) openProactive\(proactive\.offer\); else void openNote\(\) \}\}/)
  // The words come from the server like a card's; no answer → handleTunnelSelect, which shows the form.
  assert.match(widget, /const o = await proactive\.noteOpening\(stillMine, via\)[\s\S]*?if \(o\) \{[\s\S]*?openProactive\(o\)\s+\} else \{\s+handleTunnelSelect\('sales', via\)/)
  // One tap at a time, and the note stays until the chat opens (nothing hides it first).
  assert.match(widget, /if \(noteBusyRef\.current\) return\s+noteBusyRef\.current = true/)
  const openNoteBody = widget.slice(widget.indexOf('const openNote = async'), widget.indexOf('const handleBack'))
  assert.doesNotMatch(openNoteBody.split('await proactive.noteOpening(stillMine, via)')[0], /setShowAttention\(false\)/)
  // Measured apart from the button's opening.
  assert.match(widget, /posthog\?\.capture\('chat_tunnel_selected', \{ tunnel: 'sales', returning_visitor: false, via, form: false \}\)/)
})

test('the note asks the server for its words with trigger "note", the brand and the page, and gives up in time', () => {
  const body = hook.slice(hook.indexOf("noteOpening: async (stillWanted, door = 'note') =>"))
  assert.match(body, /fetchProactiveOffer\(\s+apiUrl, \{ visitor_id: ensureVisitorId\(\), trigger: 'note', trip, site: brand\.id \}, OFFER_TIMEOUT_MS,\s+\(t\) => took\.push\(t\),\s+\)/)
  // No answer in time: counted with how long it waited and why (the visitor gets the form); switched off: no word.
  assert.match(body, /if \(!res\) \{[\s\S]*?posthog\?\.capture\('proactive_note_unanswered', \{\s+door, device: [^\n]+, site: brand\.id, \.\.\.offerMeasures\(took\[0\]\),\s+\}\)\s+return null\s+\}\s+if \(!res\.show \|\| !res\.message\) return null/)
  // A turn the visitor took back meanwhile records nothing, not even the missing answer (CodeRabbit #269): Widget
  // opens no form for it.
  const owned = body.indexOf('if (stillWanted && !stillWanted()) return null')
  assert.ok(owned > body.indexOf('await fetchProactiveOffer(') && owned < body.indexOf("'proactive_note_unanswered'"))
  assert.equal(body.split('if (stillWanted && !stillWanted()) return null').length, 2, 'checked once')
  assert.match(body, /trigger: 'note', message: res\.message, quickReplies: res\.quick_replies, trip, surface: 'panel'/)
  // The visitor opened the chat: no card follows in this visit.
  assert.match(body, /ssSet\(DONE_KEY, 'chat'\)/)
  assert.match(body, /posthog\?\.capture\('proactive_shown', \{\s+trigger: 'note', surface: 'panel'/)
})

test('the corner button goes straight into a conversation too; a visitor with one gets it back; support keeps its form', () => {
  // Owner's decision (16 Nov, later): no form before the first message, from the corner button either.
  assert.match(widget, /onSelect=\{\(t\) => \(proactive\.offer \? openProactive\(proactive\.offer\) : openFromButton\(t\)\)\}/)
  const body = widget.slice(widget.indexOf('const openFromButton = '), widget.indexOf('const handleBack'))
  assert.match(body, /if \(t !== 'sales' \|\| sessionActive\) \{\s+handleTunnelSelect\(t\)\s+return\s+\}\s+void openNote\('button'\)/)
  // The note's door: the server's words (trigger "note"), the form only when they do not come in time.
  assert.match(widget, /const openNote = async \(via: 'note' \| 'button' = 'note'\) =>/)
  assert.match(hook, /trigger: 'note', trip, site: brand\.id \}, OFFER_TIMEOUT_MS/)
  // Measured apart from the note: chat_tunnel_selected says `via`, proactive_shown says `door`.
  assert.match(hook, /capture\('proactive_shown', \{\s+trigger: 'note', surface: 'panel', door,/)
  assert.match(hook, /noteOpening: \(stillWanted\?: \(\) => boolean, door\?: 'note' \| 'button'\) => Promise<ProactiveOpening \| null>/)
})

test('a note answer that comes after the visitor opened the form, or closed it, changes nothing', () => {
  // CodeRabbit: tap the note, open the form from the corner button before the words arrive — the late
  // answer replaced the form with the chat (or selected the tunnel again). Every flow takes the turn.
  const body = (from, to) => widget.slice(widget.indexOf(from), widget.indexOf(to, widget.indexOf(from)))
  for (const [from, to] of [
    ['const handleTunnelSelect = (', 'posthog?.capture'],
    ['const openProactive = (', 'proactive.opened()'],
    ['const handleBack = () => {', 'animateClose('],
    ['const handleCloseChat = () => {', "posthog?.capture('chat_closed'"],
  ]) {
    assert.match(body(from, to), /noteRunRef\.current\+\+/, from)
  }
  // The note's own ✕ while the words are on their way: the visitor said no to it.
  assert.match(widget, /onDismissAttention=\{\(\) => \{ noteRunRef\.current\+\+; setShowAttention\(false\) \}\}/)
  const openNoteBody = widget.slice(widget.indexOf('const openNote = async'), widget.indexOf('const handleBack'))
  assert.match(openNoteBody, /const run = \+\+noteRunRef\.current\s+const stillMine = \(\) => run === noteRunRef\.current/)
  // Checked after the wait and before either continuation: the chat and the form fallback.
  assert.match(openNoteBody, /await proactive\.noteOpening\(stillMine, via\)\s+\/\/[^\n]*\n\s+if \(!stillMine\(\)\) return\s+if \(o\) \{/)
  // And the hook records nothing for words that were never shown.
  const hookBody = hook.slice(hook.indexOf("noteOpening: async (stillWanted, door = 'note') =>"))
  const guard = hookBody.indexOf('if (stillWanted && !stillWanted()) return null')
  assert.ok(guard > -1)
  assert.ok(guard < hookBody.indexOf("ssSet(DONE_KEY, 'chat')") && guard < hookBody.indexOf("capture('proactive_shown'"))
})

test('an unanswered note chat comes back on the next page like a card chat', () => {
  const o = { trigger: 'note', message: "Hi, I'm Alex from BuyBusinessClass. Planning a business class trip?", quickReplies: ['Get a fare quote', 'I have a question'], trip: null, surface: 'panel', shownAt: 1 }
  assert.deepEqual(readKeptOpening(JSON.parse(JSON.stringify(keepOpening(o)))), o)
  assert.equal(readKeptOpening({ ...o, trigger: 'something_else' }), null)
})
