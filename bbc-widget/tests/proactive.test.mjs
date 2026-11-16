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
import { keepOpening, readKeptOpening } from '../src/keptOpening.ts'
import bbc from '../src/config/bbc.ts'
import bct from '../src/config/bct.ts'
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
  assert.match(hook, /fetchProactiveOffer\(\s*apiUrl, \{ visitor_id: ensureVisitorId\(\), trigger, trip, site: brand\.id \}, OFFER_TIMEOUT_MS,/)
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
  assert.match(widget, /onSelect=\{\(t\) => \(proactive\.offer \? openProactive\(proactive\.offer\) : handleTunnelSelect\(t\)\)\}/)
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
  assert.match(hook, /busy: sending\(press\.form\),/)
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
    trigger: 'kayak', message: 'Flying Atlanta → Seoul?', quickReplies: ['Yes, check fares', 'Not now'],
    trip: { source: 'kayak', origin_code: 'ATL', destination_code: 'ICN' }, surface: 'panel', shownAt: 1763250000000,
    firstReply: 'Yes, check fares',
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

test('Alex speaks on both sites as an AI travel agent, the word AI included; the server uses the same name', () => {
  for (const site of [bbc, bct]) {
    assert.equal(site.persona.name, 'Alex')
    assert.equal(site.persona.initial, 'A')
    assert.equal(site.persona.role, 'AI travel agent')
    // A travel agent, never passed off as a person: the word AI is part of the role.
    assert.match(site.persona.role, /^AI /)
  }
  // The header: the name over "AI travel agent · <brand>", the line every surface shares.
  assert.match(top, /export const personaLine = `\$\{brand\.persona\.role\} · \$\{brand\.name\}`/)
  assert.match(top, /<div class="bw-head__name" id=\{titleId\}>\{brand\.persona\.name\}<\/div>/)
  assert.match(chat, /const subtitle = lastAgentName \? `Chatting with \$\{lastAgentName\}` : personaLine/)
  // No bubble is signed "Assistant" any more, the unread preview included.
  assert.doesNotMatch(chat, /'Assistant'|<b>Assistant<\/b>/)
  // The server's openings: "Hi, I'm Alex from …" (bbc-chatbot-api/app/services/proactive.py, PERSONA).
  assert.match(read('config/types.ts'), /PERSONA\): keep them equal/)
})

test('no image can fail to load in the panel or beside the button: Alex is a letter on the brand colour', () => {
  assert.doesNotMatch(top, /<img/)
  assert.match(top, /<span class="bw-head__ava" aria-hidden="true">/)
  assert.match(chat, /const brandAvatar = <span class="bw-ava bw-ava--persona" aria-hidden="true">\{brand\.persona\.initial\}<\/span>/)
  // The logo stays only on the corner button, which the site draws a stand-in of before this script loads.
  assert.equal(buttons.match(/brand\.logoUrl/g)?.length, 1)
  assert.match(buttons, /<img src=\{brand\.logoUrl\} alt="" width=\{30\} height=\{30\} draggable=\{false\} \/>/)
  assert.match(buttons, /<span class="bw-nudge__who">\{personaAvatar\}\{personaWho\}<\/span>/)
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
  // The five of the moments, and the note's proactive_shown.
  assert.equal(captures.length, 6)
  for (const call of captures) assert.match(call, /site: brand\.id/, call.slice(0, 60))
  assert.match(api, /body: \{ visitor_id: string; trigger: string; trip: unknown; site: string \}/)
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
  // The note's own button, not the corner button: that one keeps the form.
  assert.match(buttons, /class="bw-nudge__open" onClick=\{\(\) => \(onNoteOpen \? onNoteOpen\(\) : onSelect\('sales'\)\)\}/)
  assert.match(buttons, /class="bw-launch"\s+onClick=\{\(\) => onSelect\('sales'\)\}/)
  assert.match(widget, /onNoteOpen=\{\(\) => \{ if \(proactive\.offer\) openProactive\(proactive\.offer\); else void openNote\(\) \}\}/)
  // The words come from the server like a card's; no answer → handleTunnelSelect, which shows the form.
  assert.match(widget, /const o = await proactive\.noteOpening\(stillMine\)[\s\S]*?if \(o\) \{[\s\S]*?openProactive\(o\)\s+\} else \{\s+handleTunnelSelect\('sales', 'note'\)/)
  // One tap at a time, and the note stays until the chat opens (nothing hides it first).
  assert.match(widget, /if \(noteBusyRef\.current\) return\s+noteBusyRef\.current = true/)
  const openNoteBody = widget.slice(widget.indexOf('const openNote = async'), widget.indexOf('const handleBack'))
  assert.doesNotMatch(openNoteBody.split('await proactive.noteOpening(stillMine)')[0], /setShowAttention\(false\)/)
  // Measured apart from the button's opening.
  assert.match(widget, /posthog\?\.capture\('chat_tunnel_selected', \{ tunnel: 'sales', returning_visitor: false, via: 'note', form: false \}\)/)
})

test('the note asks the server for its words with trigger "note", the brand and the page, and gives up in time', () => {
  const body = hook.slice(hook.indexOf('noteOpening: async (stillWanted) =>'))
  assert.match(body, /fetchProactiveOffer\(\s+apiUrl, \{ visitor_id: ensureVisitorId\(\), trigger: 'note', trip, site: brand\.id \}, OFFER_TIMEOUT_MS,\s+\)/)
  assert.match(body, /if \(!res \|\| !res\.show \|\| !res\.message\) return null/)
  assert.match(body, /trigger: 'note', message: res\.message, quickReplies: res\.quick_replies, trip, surface: 'panel'/)
  // The visitor opened the chat: no card follows in this visit.
  assert.match(body, /ssSet\(DONE_KEY, 'chat'\)/)
  assert.match(body, /posthog\?\.capture\('proactive_shown', \{\s+trigger: 'note', surface: 'panel'/)
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
  assert.match(openNoteBody, /await proactive\.noteOpening\(stillMine\)\s+\/\/[^\n]*\n\s+if \(!stillMine\(\)\) return\s+if \(o\) \{/)
  // And the hook records nothing for words that were never shown.
  const hookBody = hook.slice(hook.indexOf('noteOpening: async (stillWanted) =>'))
  const guard = hookBody.indexOf('if (stillWanted && !stillWanted()) return null')
  assert.ok(guard > -1)
  assert.ok(guard < hookBody.indexOf("ssSet(DONE_KEY, 'chat')") && guard < hookBody.indexOf("capture('proactive_shown'"))
})

test('an unanswered note chat comes back on the next page like a card chat', () => {
  const o = { trigger: 'note', message: "Hi, I'm Alex from BuyBusinessClass. Planning a business class trip?", quickReplies: ['Check fares', 'I have a question'], trip: null, surface: 'panel', shownAt: 1 }
  assert.deepEqual(readKeptOpening(JSON.parse(JSON.stringify(keepOpening(o)))), o)
  assert.equal(readKeptOpening({ ...o, trigger: 'something_else' }), null)
})
