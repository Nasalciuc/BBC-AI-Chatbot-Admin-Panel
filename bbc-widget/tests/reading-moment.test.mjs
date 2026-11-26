// The reading moment (owner, 25 Nov 2026, levers 3 and 4 of "Mai multe chaturi și leaduri"): the visitors who read
// before they decide get their card after READING_VISIBLE_S in view with a quarter of a screen scrolled, and a paid
// Meta visit gets a card of its own. Runs with: node --experimental-strip-types --test tests/reading-moment.test.mjs
// The places below are made up for the tests.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  ADS_VISIBLE_S, KAYAK_VISIBLE_S, READER_SCROLL_SCREENS, READER_VISIBLE_S, READING_SCROLL_SCREENS, READING_VISIBLE_S,
  adVisit, pickTrigger, readingMoment, socialVisit,
} from '../src/triggers.ts'
import { tripFromTitle } from '../src/tripContext.ts'
import { keepOpening, readKeptOpening } from '../src/keptOpening.ts'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const hook = readFileSync(join(SRC, 'proactive.ts'), 'utf8')

const page = (over = {}) => ({
  path: '/', kayak: false, ad: false, destination: false, social: false, direct: false, visibleS: 0, watchedS: 10,
  sinceActivityS: 1, scrolledScreens: 0, phone: true, sinceSiteTypingS: null, quote: null, sinceQuoteFormS: null,
  exitIntent: false, off: [], siteSignals: true, ...over,
})
const read = { visibleS: READING_VISIBLE_S, scrolledScreens: READING_SCROLL_SCREENS }

test('the reading moment is 12 s in view with a quarter of a screen scrolled', () => {
  assert.equal(READING_VISIBLE_S, 12)
  assert.equal(READING_SCROLL_SCREENS, 0.25)
  // Kayak and the computers' ads card keep their 2.5 s; the other visitors' reader keeps 25 s and half a screen.
  assert.equal(KAYAK_VISIBLE_S, 2.5)
  assert.equal(ADS_VISIBLE_S, 2.5)
  assert.equal(READER_VISIBLE_S, 25)
  assert.equal(READER_SCROLL_SCREENS, 0.5)
})

test('a paid Meta visit is known by its campaign tags, never by a fbclid alone', () => {
  assert.equal(socialVisit({ utm_source: 'facebook', utm_medium: 'paid_social' }), true)
  assert.equal(socialVisit({ utm_source: 'fb', utm_medium: 'paid' }), true)
  assert.equal(socialVisit({ utm_source: ' IG ', utm_medium: 'Paid' }), true)
  assert.equal(socialVisit({ utm_source: 'instagram', utm_medium: 'cpc' }), true)
  // Rule 5, the visits that are not it: a shared link (fbclid only), an organic post, no medium, another source.
  assert.equal(socialVisit({ utm_source: 'facebook' }), false)
  assert.equal(socialVisit({ utm_source: 'ig', utm_medium: 'social' }), false)
  assert.equal(socialVisit({}), false)
  assert.equal(socialVisit({ utm_source: 'newsletter', utm_medium: 'paid' }), false)
  // A Google or Bing click is the ads moment's, a Kayak visit Kayak's.
  assert.equal(socialVisit({ utm_source: 'facebook', utm_medium: 'paid_social', gclid: 'x' }), false)
  assert.equal(adVisit({ utm_source: 'facebook', utm_medium: 'paid_social' }), false)
  assert.equal(socialVisit({ utm_source: 'kayak', utm_medium: 'paid' }), false)
})

test('the Meta card comes at the reading moment, not sooner, and never without the read', () => {
  assert.equal(pickTrigger(page({ social: true, visibleS: 11.9, scrolledScreens: 1 })), null)
  assert.equal(pickTrigger(page({ social: true, ...read })), 'social')
  assert.equal(pickTrigger(page({ social: true, visibleS: 30, scrolledScreens: 0.2 })), null)
  // On a computer the same.
  assert.equal(pickTrigger(page({ social: true, phone: false, ...read })), 'social')
  // Not over the quote form the visitor works in (40% of these visitors open it).
  assert.equal(pickTrigger(page({ social: true, ...read, sinceQuoteFormS: 30 })), null)
  // Switched off on the server for this visit: the reader still comes, at the same reading moment.
  assert.equal(pickTrigger(page({ social: true, ...read, off: ['social'] })), 'reader')
})

test('the ads card waits for the read on a phone and keeps its 2.5 s on a computer', () => {
  assert.equal(pickTrigger(page({ ad: true, destination: true, visibleS: 2.5 })), null)
  assert.equal(pickTrigger(page({ ad: true, destination: true, visibleS: 11, scrolledScreens: 1 })), null)
  assert.equal(pickTrigger(page({ ad: true, destination: true, ...read })), 'ads')
  assert.equal(pickTrigger(page({ ad: true, destination: true, phone: false, visibleS: 2.5 })), 'ads')
  // An ad visit on a phone whose page names no place: the reader, at the reading moment too.
  assert.equal(pickTrigger(page({ ad: true, ...read })), 'reader')
  // On a computer that one keeps the old reader (25 s, half a screen).
  assert.equal(pickTrigger(page({ ad: true, phone: false, ...read })), null)
  assert.equal(pickTrigger(page({ ad: true, phone: false, visibleS: 25, scrolledScreens: 0.5 })), 'reader')
})

test('a visit with no source gets the reader at the reading moment; a visit from elsewhere keeps 25 s', () => {
  assert.equal(pickTrigger(page({ direct: true, ...read })), 'reader')
  assert.equal(pickTrigger(page({ direct: true, visibleS: 11.9, scrolledScreens: 1 })), null)
  assert.equal(pickTrigger(page({ ...read })), null)
  assert.equal(pickTrigger(page({ visibleS: 25, scrolledScreens: 0.5 })), 'reader')
  // Kayak is never on the reading moment: its card came at 2.5 s, and a Kayak visit is never "direct".
  assert.equal(pickTrigger(page({ kayak: true, direct: true, visibleS: 2.5 })), 'kayak')
})

test('the measurements name the moments that came at the reading moment', () => {
  assert.equal(readingMoment(page({ social: true }), 'social'), true)
  assert.equal(readingMoment(page({ ad: true, phone: true }), 'ads'), true)
  assert.equal(readingMoment(page({ ad: true, phone: false }), 'ads'), false)
  assert.equal(readingMoment(page({ direct: true }), 'reader'), true)
  assert.equal(readingMoment(page({ ad: true, phone: true }), 'reader'), true)
  assert.equal(readingMoment(page({}), 'reader'), false)
  assert.equal(readingMoment(page({ kayak: true }), 'kayak'), false)
  assert.equal(readingMoment(page({ direct: true }), 'quote'), false)
  // proactive.ts reads them on the page and carries them on the events.
  assert.match(hook, /social: socialVisitNow\(\),\n\s+direct: directVisitNow\(\),/)
  assert.match(hook, /void ask\(trigger, state\.phone, readingMoment\(state, trigger\)\)/)
  assert.equal((hook.match(/\.\.\.\(reading \? \{ moment: 'reading' \} : \{\}\)/g) ?? []).length, 2, 'proactive_eligible and proactive_shown')
})

test('a Meta page titled for a region names it as the card\'s place', () => {
  assert.deepEqual(tripFromTitle('Business class to Asia'), { source: 'route_page', destination_name: 'Asia', cabin: 'business' })
  assert.deepEqual(tripFromTitle('First class to the Middle East'), {
    source: 'route_page', destination_name: 'Middle East', cabin: 'first',
  })
  assert.deepEqual(tripFromTitle('Business Class to South-East Asia'), {
    source: 'route_page', destination_name: 'South-East Asia', cabin: 'business',
  })
  // A tagline names no place: the card asks without one.
  assert.equal(tripFromTitle('Business class to Europe for less'), null)
  assert.equal(tripFromTitle('First class to anywhere'), null)
  assert.equal(tripFromTitle('Business class to Asia, Europe and the Americas at Private Fares'), null)
  // The titles it read before read the same.
  assert.deepEqual(tripFromTitle('Business class flights to Lakeside'), { source: 'route_page', destination_name: 'Lakeside', cabin: 'business' })
  assert.equal(tripFromTitle('Business class'), null)
})

test('a Meta card kept with the open panel reads back on the next page', () => {
  const o = { trigger: 'social', message: 'Hi', quickReplies: ['Yes, send me fares'], trip: null, surface: 'card', shownAt: 1 }
  const back = readKeptOpening(JSON.parse(JSON.stringify(keepOpening(o))))
  assert.equal(back?.trigger, 'social')
})
