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
import { hasContext, readTrip, tripFromTitle, tripFromUrl } from '../src/tripContext.ts'
import {
  EXIT_AFTER_S, KAYAK_VISIBLE_S, QUOTE_IDLE_S, QUOTE_INVALID_IDLE_S, READER_VISIBLE_S,
  blockedPath, isNotNow, pauseRunning, pickTrigger, replyKind,
} from '../src/triggers.ts'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const read = (f) => readFileSync(join(SRC, f), 'utf8')
const chat = read('ChatWindow.tsx')
const widget = read('Widget.tsx')
const buttons = read('FloatingButtons.tsx')
const hook = read('proactive.ts')
const api = read('api.ts')

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
  visibleS: 0,
  sinceActivityS: 0,
  scrolledScreens: 0,
  phone: false,
  finePointer: true,
  sinceSiteTypingS: null,
  quote: null,
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

test('exit: computers only, after 15 s', () => {
  assert.equal(at({ exitIntent: true, visibleS: EXIT_AFTER_S }), 'exit')
  assert.equal(at({ exitIntent: true, visibleS: EXIT_AFTER_S - 1 }), null)
  assert.equal(at({ exitIntent: true, visibleS: 60, phone: true }), null)
  assert.equal(at({ exitIntent: true, visibleS: 60, finePointer: false }), null)
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
})

test('kayak before reader before exit', () => {
  assert.equal(at({ kayak: true, visibleS: 40, scrolledScreens: 2, exitIntent: true }), 'kayak')
  assert.equal(at({ visibleS: 40, scrolledScreens: 2, exitIntent: true }), 'reader')
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
  const replies = ['Yes, check fares', 'Change details', 'Not now']
  assert.equal(replyKind('Yes, check fares', replies), 'button_1')
  assert.equal(replyKind('change details', replies), 'button_2')
  assert.equal(replyKind('my email is …', replies), 'text')
})

// ── The wiring (source pins) ─────────────────────────────────────────────────────────

test('the card-opened chat makes no /chat/start: the first message creates the conversation', () => {
  const mount = chat.slice(chat.indexOf('// AI opens on fresh sessions'))
  assert.ok(mount.indexOf('if (openingRef.current) {') < mount.indexOf('startConversation()'))
  assert.match(chat, /\.\.\.\(opening \? \{ trip: opening\.trip, proactive: \{ trigger: opening\.trigger \} \} : \{\}\)/)
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

test('the offer has its 1.5 s answer or nothing', () => {
  assert.match(api, /\/api\/chat\/proactive\/offer/)
  assert.match(api, /ctrl\?\.abort\(\)/)
  assert.match(hook, /fetchProactiveOffer\(\s*apiUrl, \{ visitor_id: ensureVisitorId\(\), trigger, trip \}, OFFER_TIMEOUT_MS,/)
})

test('the guards: one moment per visit, the 24 h pause, the closed note, the CRM iframe', () => {
  assert.match(hook, /if \(ssGet\(DONE_KEY\) \|\| ssGet\(NOTE_CLOSED_KEY\) \|\| pauseRunning\(lsGet\(PAUSE_KEY\), Date\.now\(\)\)\) return/)
  assert.match(hook, /if \(!allowed \|\| step !== 'buttons'\) return/)
  assert.match(widget, /useProactive\(\{ apiUrl, step, allowed: !embedded && !hasOptimisticSession \}\)/)
  assert.match(buttons, /onClick=\{\(\) => \{ markNoteClosed\(\); onDismissAttention\?\.\(\) \}\}/)
  // The card replaces the note for the rest of the visit.
  assert.match(widget, /showAttention=\{showAttention && !sessionActive && !proactive\.quiet\}/)
})

test('a conversation the card opened is the visitor\'s session: the button restores it, no form', () => {
  assert.match(widget, /const sessionActive = !!\(hasOptimisticSession && \(savedVisitor \|\| proactiveConv\)\)/)
  assert.match(widget, /if \(hasOptimisticSession && \(savedVisitor \|\| proactiveConv\)\) \{/)
})

test('a phone gets the card, a computer the panel', () => {
  assert.match(hook, /const surface: Surface = phone \? 'card' : 'panel'/)
  assert.match(widget, /if \(proactive\.offer\?\.surface === 'panel'\) openProactive\(proactive\.offer\)/)
  assert.match(widget, /card=\{proactive\.offer\?\.surface === 'card' \? proactive\.offer : null\}/)
})
