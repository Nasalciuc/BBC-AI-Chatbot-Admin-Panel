/**
 * What follows the card (18 Nov 2026, "12 probleme Kayak și chat"): the instant answer to its yes and the
 * channel buttons (A1, A2), the client's own mobile number for SMS or email (the owner, 18 Nov: "spune că îi
 * vom scrie, unde: pe email și WhatsApp"; 20 Nov: SMS in place of WhatsApp everywhere), the card's channel
 * button and the pill after "Not now" (C1, C2), the row
 * beside the refused quote form (D1), one message before a contactless client leaves (B1), popups (E1),
 * the keyboard (E2), the card in Spanish and French (ADR-9b), and the Kayak card's sooner moment (ADR A1, which
 * retired the C4 test).
 *
 * Pure parts tested directly; the wiring pinned on the source. The server's half is
 * bbc-chatbot-api/tests/test_card_buttons.py. Every contact here is invented.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  guessCountry, ownerOf, phoneFor, readRowContact, readablePhone, rowField, rowShowsCountry, typedEmail, validEmail, validPhone,
} from '../src/channel.ts'
import { COUNTRIES } from '../src/countries.ts'
import { channelMessage, widgetLang, words } from '../src/i18n.ts'
import { TITLE_MAX, askBeforeYouGo, fetchProactiveOffer, readButtons, readChannel } from '../src/api.ts'
import {
  ADS_VISIBLE_S, KAYAK_VISIBLE_S, QUOTE_INVALID_IDLE_S, QUOTE_ROW_IDLE_S, WATCHED_FIRST_S, WATCHED_FIRST_SIGNALS_S,
  adKeyword, givesMomentBack, givesRowBack, isNotNow, keywordNamesPlace, pickTrigger, rowAgainOpen, rowMoment,
} from '../src/triggers.ts'
import { LAYOUT_AFTER_MS, cardAsideWatch, coveredShare, watchPopups } from '../src/overlay.ts'
import { keyboardInset } from '../src/keyboard.ts'
import { keepOpening, readKeptOpening } from '../src/keptOpening.ts'
import { LOCAL_AI_PREFIX, reconcileBatch } from '../src/reconcile.ts'
import { ROW_CSS, WIDGET_CSS } from '../src/styles.ts'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const read = (f) => readFileSync(join(SRC, f), 'utf8')
const chat = read('ChatWindow.tsx')
const widget = read('Widget.tsx')
const buttons = read('FloatingButtons.tsx')
const hook = read('proactive.ts')
const row = read('FormRow.tsx')

const AU = COUNTRIES.find(c => c.code === 'AU')
const US = COUNTRIES[0]

// ── The client's contact: a mobile number for SMS, or email ──────────────────────────────────

test('a number is placed in its country: its own code first, then the country beside the field', () => {
  assert.equal(phoneFor('0412 345 678', AU), '+61412345678')         // #3296: the trunk 0 dropped
  assert.equal(phoneFor('+61 412 345 678', US), '+61412345678')      // its own code wins
  assert.equal(phoneFor('0061 412 345 678', US), '+61412345678')
  assert.equal(phoneFor('(210) 555-0100', US), '+12105550100')
  assert.equal(phoneFor('1 210 555 0100', US), '+12105550100')
  assert.equal(phoneFor('12 34', AU), '')
  assert.equal(validPhone(COUNTRIES, '0412 345 678', AU), '+61412345678')
  assert.equal(validPhone(COUNTRIES, '0412 345', AU), null)       // too short for Australia
  assert.equal(validPhone(COUNTRIES, '210 555 0100', US), '+12105550100')
  assert.equal(readablePhone(COUNTRIES, '+61412345678'), '+61 412 345 678')
  assert.equal(ownerOf(COUNTRIES, '+971501234567').code, 'AE')
  // Italy dials its leading 0 from abroad too (CodeRabbit #270): never +39 2 1234 5678.
  const IT = COUNTRIES.find(c => c.code === 'IT')
  assert.equal(phoneFor('02 1234 5678', IT), '+390212345678')
  assert.equal(validPhone(COUNTRIES, '02 1234 5678', IT), '+390212345678')
  assert.equal(phoneFor('312 345 6789', IT), '+393123456789')         // a mobile has no 0
  assert.equal(phoneFor('0039 02 1234 5678', AU), '+390212345678')
})

test('the country beside the field is guessed from the browser, never from the number', () => {
  assert.equal(guessCountry(COUNTRIES, 'en-AU', '').code, 'AU')
  assert.equal(guessCountry(COUNTRIES, 'en', 'Australia/Sydney').code, 'AU')
  assert.equal(guessCountry(COUNTRIES, 'fr', 'Europe/Paris').code, 'FR')
  assert.equal(guessCountry(COUNTRIES, 'en-US', 'Europe/London').code, 'US')  // the language's region first
  assert.equal(guessCountry(COUNTRIES, '', 'Mars/Base').code, 'US')
})

test('an email is one, or nothing', () => {
  assert.equal(validEmail(' client@example.com '), 'client@example.com')
  for (const bad of ['client@example', 'client example.com', '@example.com', 'a@b.c', '']) {
    assert.equal(validEmail(bad), null, bad)
  }
})

test('an email is what the server reads: the field sends what it keeps, and nothing else (25 Nov)', () => {
  // The same cases as the server's (bbc-chatbot-api tests/test_card_buttons.py, "read whole as the widget's field
  // checks it"). An email typed with stars went out on 25 Nov, the server found none in it, and the model said it
  // was captured; "john*doe@…" was kept as "doe@…".
  const kept = [
    'client@example.com', 'First.Last+tag@Sub.Example.co.uk', 'a_b%c-d@x-y.io',
    'a'.repeat(108) + '@example.com',                                          // 120 characters
  ]
  for (const ok of kept) assert.equal(validEmail(ok), ok, ok)
  const refused = [
    'j***@gmail.com', 'john*doe@gmail.com', "o'brien@example.com", 'client@example', 'client example.com',
    '@example.com', 'a@b.c', '-x@example.com', '.x@example.com', 'x@example.com.', 'x@@example.com',
    'x@exa_mple.com', 'x@example.c0m', '',
    'a'.repeat(109) + '@example.com',                                          // 121
  ]
  for (const bad of refused) assert.equal(validEmail(bad), null, bad)
  // The row beside the refused form reads its email the same way.
  assert.deepEqual(readRowContact('j***@gmail.com', COUNTRIES, COUNTRIES[0]), { error: 'email' })
})

test('what goes out as the client\'s message is what the agent reads, in the client\'s language', () => {
  assert.equal(channelMessage('en', 'sms', '+61 412 345 678'), 'Text message: +61 412 345 678')
  assert.equal(channelMessage('es', 'sms', '+34 612 345 678'), 'SMS: +34 612 345 678')
  assert.equal(channelMessage('fr', 'sms', '+33 6 12 34 56 78'), 'SMS : +33 6 12 34 56 78')
  assert.equal(channelMessage('es', 'email', 'cliente@example.com', '4,200'), 'Correo: cliente@example.com · Precio visto: 4,200')
  assert.equal(channelMessage('fr', 'email', 'client@example.com'), 'E-mail : client@example.com')
  assert.equal(widgetLang({ language: 'es-MX' }), 'es')
  assert.equal(widgetLang({ language: 'fr-CA' }), 'fr')
  assert.equal(widgetLang({ language: 'pt-BR' }), 'en')
  for (const lang of ['en', 'es', 'fr']) {
    const w = words(lang)
    for (const k of Object.keys(words('en'))) assert.ok(w[k] && w[k].trim(), `${lang}.${k}`)
  }
})

// ── The server's buttons and the offer ─────────────────────────────────────────────────────

test('only buttons the widget knows how to act on pass', () => {
  assert.deepEqual(
    readButtons([{ kind: 'sms', label: 'Text message' }, { kind: 'call', label: 'Call' }, { kind: 'email', label: ' ' },
      { kind: 'wait', label: "I'll wait here" }, 'x', null]),
    [{ kind: 'sms', label: 'Text message' }, { kind: 'wait', label: "I'll wait here" }],
  )
  assert.equal(readChannel({ kind: 'wait', label: 'x' }), null)
  // SMS in place of WhatsApp everywhere (20 Nov): the widget offers no WhatsApp, whatever the server says.
  assert.deepEqual(readButtons([{ kind: 'whatsapp', label: 'WhatsApp' }]), [])
  assert.equal(readChannel({ kind: 'whatsapp', label: 'Fares on WhatsApp' }), null)
  assert.deepEqual(readChannel({ kind: 'sms', label: 'Fares by text' }), { kind: 'sms', label: 'Fares by text' })
  assert.deepEqual(readChannel({ kind: 'email', label: 'Email me fares' }), { kind: 'email', label: 'Email me fares' })
})

test('the offer brings what follows the yes, the card\'s channel, the row\'s buttons and the language', async () => {
  const realFetch = globalThis.fetch
  let sent = null
  globalThis.fetch = async (url, init) => {
    sent = JSON.parse(init.body)
    return new Response(JSON.stringify({
      show: true, arm: 'proactive', message: 'Hola', quick_replies: ['Sí, envíenme tarifas', 'Ahora no'], lang: 'es',
      confirm: { message: 'Perfecto: …', buttons: [{ kind: 'sms', label: 'SMS' }, { kind: 'nope', label: 'x' }] },
      channel: { kind: 'sms', label: 'Tarifas por SMS' }, variant: 'beat',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }
  try {
    const res = await fetchProactiveOffer('https://api.test', {
      visitor_id: 'visitor-0123456789', trigger: 'kayak', trip: null, site: 'bbc', lang: 'es-MX', device: 'phone',
    }, 3500)
    assert.equal(sent.lang, 'es-MX')
    assert.equal(sent.device, 'phone')
    assert.equal(sent.c4, undefined)
    assert.equal(res.lang, 'es')
    assert.equal(res.variant, 'beat')
    assert.deepEqual(res.confirm, { message: 'Perfecto: …', buttons: [{ kind: 'sms', label: 'SMS' }] })
    assert.deepEqual(res.channel, { kind: 'sms', label: 'Tarifas por SMS' })
  } finally {
    globalThis.fetch = realFetch
  }
})

test('before you go: the server\'s message with its buttons, or nothing', async () => {
  const realFetch = globalThis.fetch
  globalThis.fetch = async () => new Response(JSON.stringify({
    show: true, message: 'Before you go — …', id: 'm9', created_at: '2026-11-18T10:00:00+00:00',
    buttons: [{ kind: 'sms', label: 'Text message' }, { kind: 'email', label: 'Email' }],
  }), { status: 200 })
  try {
    const out = await askBeforeYouGo('https://api.test', 'conv-1')
    assert.equal(out.show, true)
    assert.equal(out.buttons.length, 2)
    globalThis.fetch = async () => new Response(JSON.stringify({ show: false, why: 'has_contact' }), { status: 200 })
    assert.deepEqual(await askBeforeYouGo('https://api.test', 'conv-1'), { show: false, why: 'has_contact' })
    globalThis.fetch = async () => new Response(JSON.stringify({ show: false }), { status: 200 })
    assert.deepEqual(await askBeforeYouGo('https://api.test', 'conv-1'), { show: false })
    globalThis.fetch = async () => { throw new Error('offline') }
    assert.equal(await askBeforeYouGo('https://api.test', 'conv-1'), null)
  } finally {
    globalThis.fetch = realFetch
  }
})

// ── The moments: "Not now" in three languages, the Kayak card sooner, the row's sooner moment ─────────

const page = (over = {}) => ({
  path: '/', kayak: false, ad: false, destination: false, visibleS: 0, watchedS: 10, sinceActivityS: 1,
  scrolledScreens: 0, phone: true, sinceSiteTypingS: null, quote: null, sinceQuoteFormS: null, exitIntent: false,
  off: [], ...over,
})

test('"Not now" is heard in Spanish and French too, and never reaches the server', () => {
  for (const no of ['Not now', 'Ahora no', 'Pas maintenant', ' pas maintenant ']) assert.equal(isNotNow(no), true, no)
  assert.equal(isNotNow('Ahora no, gracias'), false)
})

test('ADR A1: the Kayak card at 2.5 s in view, after half a second of watching on a page with the site\'s signals', () => {
  assert.equal(KAYAK_VISIBLE_S, 2.5)
  assert.equal(pickTrigger(page({ kayak: true, visibleS: 2.4 })), null)
  assert.equal(pickTrigger(page({ kayak: true, visibleS: 2.5 })), 'kayak')
  // The site's signals replay a quote form opened before the widget started: half a second of watching is enough.
  assert.equal(WATCHED_FIRST_SIGNALS_S, 0.5)
  assert.equal(pickTrigger(page({ kayak: true, visibleS: 4, watchedS: 0.6, siteSignals: true })), 'kayak')
  // Without them the widget still watches WATCHED_FIRST_S first.
  assert.equal(pickTrigger(page({ kayak: true, visibleS: 4, watchedS: 0.6 })), null)
  assert.equal(pickTrigger(page({ kayak: true, visibleS: 4, watchedS: WATCHED_FIRST_S })), 'kayak')
  assert.equal(pickTrigger(page({ kayak: true, visibleS: 4, watchedS: 0.4, siteSignals: true })), null)
  // The hook says whether the page sends them, and asks no C4 side any more.
  assert.match(hook, /siteSignals: !!sitePage\(\),/)
  assert.doesNotMatch(hook, /c4/i)
})

test('D1: a refused send beside a form the widget sees gets its row at once, sooner than a card', () => {
  // Owner, 23 Nov: at once (it was 2 s). 71% of the refused visitors left within 5 s.
  assert.equal(QUOTE_ROW_IDLE_S, 0)
  const quote = { sincePressS: 0, sinceFormActivityS: 0, invalid: true, busy: false }
  assert.equal(pickTrigger(page({ quote: { ...quote, row: true } })), 'quote')
  // Without a form to stand beside, the card still waits for the form to go quiet; a send not refused waits too.
  assert.equal(pickTrigger(page({ quote })), null)
  assert.equal(pickTrigger(page({ quote: { ...quote, row: true, invalid: false } })), null)
  assert.ok(QUOTE_ROW_IDLE_S < QUOTE_INVALID_IDLE_S)
  assert.match(hook, /const rowAt = trigger === 'quote' && press\?\.form\?\.isConnected \? press\.form : null/)
  assert.match(hook, /const surface: Surface = row \? 'row' : phone \? 'card' : 'panel'/)
  // The refusal is heard at once, the browser's and the site's, not at the next second of the clock.
  assert.match(hook, /refused = withKinds\(refused, \[refusedKind\(e\.target\)\]\)\s+refusedNow\(\)/)
  assert.match(hook, /if \(press\?\.invalid && \(siteRefused \|\| e\.name === 'quote_form_failed'\)\) refusedNow\(\)/)
  assert.match(hook, /refusalTimer = setTimeout\(\(\) => \{\s+refusalTimer = null\s+evaluate\(\)\s+\}, 0\)/)
  // A visitor fixing the form while the row's answer is on its way still gets it; a new press drops it.
  assert.match(hook, /const forRow = trigger === 'quote' && before\.row/)
  assert.match(hook, /if \(!forRow && lastSiteTyping !== before\.typing\) return 'typing'/)
  assert.match(hook, /if \(\(press\?\.at \?\? null\) !== before\.pressAt \|\| \(!forRow && \(press\?\.activityAt \?\? null\) !== before\.formAt\)\) return 'quote_form'/)
  // The contact left in the row opens the chat with it sent as the client's first message.
  assert.match(widget, /return placeFormRow\(\s+form, o,\s+\(kind, contact\) => openProactive\(\{\s+\.\.\.o, surface: 'panel', firstContact: \{ kind, text: channelMessage\(widgetLang\(\), kind, contact\) \},/)
  assert.match(chat, /const \{ kind, text \} = o\.firstContact\s+onProactiveEngaged\?\.\(text\)\s+posthog\?\.capture\('chat_channel_given', \{ channel: kind, tunnel, where: 'row', \.\.\.fieldTestRef\.current \}\)\s+sendMessage\(text, \{ button: kind \}\)/)
  // The row stands under the pressed button, outside the widget, in its own tree: its field belongs to no form of
  // the site, its keys and typing never reach the site's form scripts, and the site's styles never reach it.
  assert.match(row, /const button = form\.querySelector\('button\[type="submit"\], input\[type="submit"\], button:not\(\[type\]\)'\)/)
  assert.match(row, /at\.insertAdjacentElement\(where, host\)/)
  assert.match(row, /if \(typeof host\.attachShadow !== 'function'\) return \(\) => \{\}/)
  assert.match(row, /const root = host\.attachShadow\(\{ mode: 'open' \}\)/)
  assert.match(row, /for \(const type of KEPT_IN_ROW\) host\.addEventListener\(type, keep\)/)
  assert.match(row, /type="text"/)
  assert.match(row, /if \(e\.key !== 'Enter'\) return\s+e\.preventDefault\(\)/)
  assert.match(ROW_CSS, /button \{ all: unset;/)
  assert.match(ROW_CSS, /\.bw-row-offer__input \{[^}]*font-size: 16px/)
  assert.doesNotMatch(WIDGET_CSS, /\.bw-row-offer/)
})

test('D1: the row takes an email or a mobile number, and says one is enough to a visitor who gave the form one', () => {
  // The kinds the form refused, never what was typed: a refused phone and no refused email gave the email.
  assert.equal(rowField(['phone']), 'email')
  assert.equal(rowField(['name', 'phone']), 'email')
  assert.equal(rowField(['email']), 'sms')
  assert.equal(rowField(['email', 'phone']), 'any')
  assert.equal(rowField(['name']), 'any')
  assert.equal(rowField([]), 'any')
  assert.equal(rowField(null), 'any')
  // Either contact goes in, whatever the field asked for; a number typed the local way is placed in its country.
  assert.deepEqual(readRowContact(' client@example.com ', COUNTRIES, US), { kind: 'email', contact: 'client@example.com' })
  assert.deepEqual(readRowContact('0412 345 678', COUNTRIES, AU), { kind: 'sms', contact: '+61 412 345 678' })
  assert.deepEqual(readRowContact('+1 210 555 0100', COUNTRIES, AU), { kind: 'sms', contact: '+1 210 555 0100' })
  assert.deepEqual(readRowContact('client@example', COUNTRIES, US), { error: 'email' })
  assert.deepEqual(readRowContact('0412 345', COUNTRIES, AU), { error: 'phone' })
  assert.deepEqual(readRowContact('', COUNTRIES, US), { error: 'either' })
  assert.deepEqual(readRowContact('London', COUNTRIES, US), { error: 'either' })
  // Its words in the three languages, with no WhatsApp.
  for (const lang of ['en', 'es', 'fr']) {
    const w = words(lang)
    for (const k of ['rowAny', 'rowSend', 'rowOneEmail', 'rowOneSms', 'badEither']) assert.ok(w[k] && w[k].trim(), `${lang}.${k}`)
  }
  assert.equal(words('en').rowSend, 'Send me fares')
  assert.match(row, /const one = field === 'email' \? w\.rowOneEmail : field === 'sms' \? w\.rowOneSms : ''/)
  // The hook gives the row its field from the refused kinds, and measures it: the kinds the server was asked with,
  // one copy for the request and the row, whatever the site says of the same send meanwhile (CodeRabbit #279).
  assert.match(hook, /const refusedAsked = trigger === 'quote' \? \[\.\.\.refused\] : \[\]/)
  assert.match(hook, /\.\.\.\(refusedAsked\.length \? \{ refused: refusedAsked \} : \{\}\),/)
  assert.match(hook, /\.\.\.\(row \? \{ rowField: rowField\(refusedAsked\) \} : \{\}\),/)
  assert.doesNotMatch(hook, /rowField\(refused\)/)
  assert.match(hook, /\.\.\.\(opening\.rowField \? \{ row_field: opening\.rowField \} : \{\}\),/)
  // Neither the contact nor the row's field is ever kept with the opening across pages.
  const kept = keepOpening({ trigger: 'quote', message: 'Faster than the form', quickReplies: [], trip: null, surface: 'row',
    shownAt: 1, variant: 'row', rowField: 'email', firstContact: { kind: 'email', text: 'Email: client@example.com' } })
  assert.equal('firstContact' in kept, false)
  assert.equal('rowField' in kept, false)
})

test('D1: the row shows the country it places a number in, and the visitor can change it (CodeRabbit #279)', () => {
  // An en-GB browser guessed the UK for a US number typed without its code, unseen: the number went out as +44.
  const GB = COUNTRIES.find(c => c.code === 'GB')
  assert.equal(readRowContact('210 555 0100', COUNTRIES, GB).contact.startsWith('+44'), true)
  assert.deepEqual(readRowContact('210 555 0100', COUNTRIES, US), { kind: 'sms', contact: '+1 210 555 0100' })
  // Shown on the mobile-number row at once, on the others as soon as what is typed looks like a number.
  assert.equal(rowShowsCountry('sms', ''), true)
  assert.equal(rowShowsCountry('sms', '210 555 0100'), true)
  assert.equal(rowShowsCountry('any', ''), false)
  assert.equal(rowShowsCountry('any', '210 555'), true)
  assert.equal(rowShowsCountry('email', '(210) 555-0100'), true)
  // A number with its own code, an email, or words need none.
  for (const field of ['any', 'sms', 'email']) {
    assert.equal(rowShowsCountry(field, '+1 210 555 0100'), false)
    assert.equal(rowShowsCountry(field, '0044 20 7946 0000'), false)
    assert.equal(rowShowsCountry(field, 'client@example.com'), false)
  }
  assert.equal(rowShowsCountry('any', 'call me 210'), false)
  // The row reads the number with the country it shows: the guess first, the visitor's pick after.
  assert.match(row, /const \[country, setCountry\] = useState<Country>\(\(\) => guessCountry\(COUNTRIES, browserLanguage\(\), timeZone\(\)\)\)/)
  assert.match(row, /const read = readRowContact\(value, COUNTRIES, country\)/)
  assert.match(row, /const showCountry = rowShowsCountry\(field, value\)/)
  assert.match(row, /\{showCountry && \(\s+<select\s+class="bw-row-offer__cc"\s+aria-label=\{w\.country\}\s+value=\{country\.code\}/)
  assert.match(row, /if \(picked\) setCountry\(picked\)/)
  assert.match(row, /\{COUNTRIES\.map\(c => <option key=\{c\.code\} value=\{c\.code\}>\{`\$\{c\.flag\} \$\{c\.dial\}`\}<\/option>\)\}/)
  // As the field: 16px, so a phone does not zoom in on it.
  assert.match(ROW_CSS, /\.bw-row-offer__cc \{[^}]*font-size: 16px/)
  // Its change stays in the row like its keys and typing.
  assert.match(row, /const KEPT_IN_ROW = \[[^\]]*'change'/)
})

test("D1: the row's contact reaches the chat when an agent's hello holds the opening's place (CodeRabbit #279)", () => {
  // The hello takes the opening's place at mount (liveRef), which dropped the row's contact with the opening.
  assert.match(chat, /const openingRef = useRef<ProactiveOpening \| null>\(!savedConvId && proactive && !liveRef\.current \? proactive : null\)/)
  // It still goes out, as the answer to the hello: the send carries the hello's tap.
  assert.match(chat, /const rowContact = liveRef\.current \? proactive\?\.firstContact : undefined\s+if \(rowContact\) \{\s+onProactiveEngaged\?\.\(rowContact\.text\)/)
  assert.match(chat, /sendMessage\(rowContact\.text, \{ button: rowContact\.kind \}\)\s+return\s+\}\s+\/\/ An agent's hello is on screen: nothing exists on the server until the visitor answers it\.\s+if \(liveRef\.current\) return/)
  assert.match(chat, /const hello = convId \? null : liveRef\.current/)
  assert.match(chat, /\.\.\.\(hello \? \{ live_tap: hello\.tap \} : \{\}\),/)
})

// ── E1: popups; E2: the keyboard ──────────────────────────────────────────────────────────

test('E1: something fixed of the site over a third of the screen is a popup — for a minute, then layout', () => {
  assert.equal(coveredShare({ left: 0, top: 0, width: 400, height: 800 }, 400, 800), 1)
  assert.ok(coveredShare({ left: 0, top: 0, width: 400, height: 60 }, 400, 800) < 0.3)  // a header
  let now = 0
  const popups = watchPopups(() => now)
  const modal = { contains: () => false }
  assert.equal(popups.open(undefined, []), false)
  assert.equal(popups.open(undefined, [modal]), true)
  now += LAYOUT_AFTER_MS - 1
  assert.equal(popups.open(undefined, [modal]), true)
  now += 2
  assert.equal(popups.open(undefined, [modal]), false)                 // a minute without a break: the page's own
  assert.equal(popups.open(undefined, []), false)
  assert.equal(popups.open(undefined, [modal]), true)                  // gone and back: a popup again
  assert.equal(popups.open(() => true, [modal]), false)                // the quote form's own modal
  assert.match(hook, /if \(popups\.open\(trigger === 'quote' \?/)
  assert.match(hook, /posthog\?\.capture\('proactive_popup_held', \{ trigger,/)
  // The answer that arrives under a popup waits for it — the quote card too, past its own form's modal
  // (CodeRabbit #270: a site popup opened while the quote request was pending, and the card showed over it).
  assert.match(hook, /const ignore = trigger === 'quote' \? \(el: Element\) => !!pressed && el\.contains\(pressed\) : undefined/)
  assert.match(hook, /res\.message && popups\.open\(ignore\)\) \{\s+await untilPopupCloses\(ignore\)/)
  assert.doesNotMatch(hook, /trigger !== 'quote' && popups\.open\(\)/)
})

test('E1 after the card: a popup that opens over the card puts it aside until the popup goes (secret-site, 22 Nov)', () => {
  let now = 0
  let covering = []
  const w = watchPopups(() => now)
  const heard = []
  const look = cardAsideWatch({ open: () => w.open(undefined, covering) }, (on) => heard.push([now, on]))
  look()                                            // nothing over the page: the card stays
  now = 2500
  look()
  const modal = { contains: () => false }
  now = 5000; covering = [modal]; look()            // the site's "$100" popup at 5 s: the card steps aside
  now = 6000; look()                                // still open: nothing new
  now = 8000; covering = []; look()                 // closed: the card comes back
  now = 9000; covering = [modal]; look()            // open again: aside again
  now = 9000 + LAYOUT_AFTER_MS; look()              // a minute without a break: the page's layout, the card returns over it
  assert.deepEqual(heard, [[5000, true], [8000, false], [9000, true], [9000 + LAYOUT_AFTER_MS, false]])
  // Watched only while the phone card is on screen (the computer's panel has room); while aside the corner button
  // stands alone and a tap on it still opens Alex's chat with the card's words.
  assert.match(hook, /if \(!offer \|\| offer\.surface !== 'card' \|\| step !== 'buttons'\) return\s+const o = offer\s+(\/\/[^\n]*\s+)*const form = o\.trigger === 'quote' \? cardFormRef\.current : null\s+const look = cardAsideWatch\(pagePopups, \(on\) => \{/)
  // One watch for the page: the minute a popup has covered it is counted once, by the moments before the card
  // and by the card after (a second watch started its own minute: the card at 184 s in the sandbox, 23 Nov).
  assert.match(hook, /const \[pagePopups\] = useState\(watchPopups\)/)
  assert.match(hook, /const popups = pagePopups\n/)
  assert.doesNotMatch(hook, /watchPopups\(\)/)
  assert.match(hook, /posthog\?\.capture\(on \? 'proactive_card_aside' : 'proactive_card_back', \{ trigger: o\.trigger, site: brand\.id \}\)/)
  assert.match(hook, /const t = setInterval\(look, 250\)\s+return \(\) => clearInterval\(t\)\s+\}, \[offer, step\]\)/)
  assert.match(widget, /card=\{proactive\.offer\?\.surface === 'card' && !proactive\.aside \? proactive\.offer : null\}/)
  assert.match(widget, /onSelect=\{\(t\) => \(proactive\.offer \? openProactive\(proactive\.offer\) : openFromButton\(t\)\)\}/)
})

test('E1 after the card: the quote card looks past its own form\'s modal, any other popup still puts it aside (CodeRabbit #276)', () => {
  let covering = []
  const w = watchPopups(() => 0)
  const form = { id: 'the pressed quote form' }
  const formModal = { contains: (el) => el === form }
  const sitePopup = { contains: () => false }
  const heard = []
  const ignore = (el) => el.contains(form)
  const look = cardAsideWatch({ open: (ig) => w.open(ig, covering) }, (on) => heard.push(on), ignore)
  covering = [formModal]; look()                    // the visitor is on the quote form's modal: the card stays
  assert.deepEqual(heard, [])
  covering = [formModal, sitePopup]; look()         // the site's own popup over it: aside
  covering = [formModal]; look()                    // gone again: back
  assert.deepEqual(heard, [true, false])
  // Without an exception (every other card) the same modal is a popup like any other.
  const other = []
  const plain = cardAsideWatch({ open: (ig) => watchPopups(() => 0).open(ig, [formModal]) }, (on) => other.push(on))
  plain()
  assert.deepEqual(other, [true])
  // The hook keeps the form the quote card's moment was pressed on, and hands its exception to the watch.
  assert.match(hook, /const cardFormRef = useRef<Element \| null>\(null\)/)
  assert.match(hook, /shownRef\.current = opening\s+cardFormRef\.current = trigger === 'quote' \? pressed : null\s+setQuiet\(true\)\s+setOffer\(opening\)/)
  assert.match(hook, /\}, form \? \(el: Element\) => el\.contains\(form\) : undefined\)/)
})

test('E1: a popup\'s minute runs from when it appears, through Alex\'s wait too (22 Nov: 185 s, now 125 s)', () => {
  const start = hook.indexOf('const evaluate = () => {')
  const evaluate = hook.slice(start, hook.indexOf('const tick = setInterval(evaluate, 1000)', start))
  const look = evaluate.indexOf('popups.open()\n')
  assert.ok(look > 0, 'the evaluate loop keeps the popup clock')
  assert.ok(look < evaluate.indexOf('if (alexWaitLeftMs(now) > 0) return'), 'before Alex\'s wait returns')
  assert.ok(look < evaluate.indexOf('const trigger = pickTrigger(state)'), 'before a moment is picked')
  // With the clock kept each second: a popup open since 5 s is the page's layout when the two minutes end, so
  // the card comes; one that opened during the wait (at 100 s) still holds it at 125 s.
  const modal = { contains: () => false }
  let now = 0
  const early = watchPopups(() => now)
  for (now = 5000; now <= 125000; now += 1000) early.open(undefined, [modal])
  assert.equal(early.open(undefined, [modal]), false)
  const late = watchPopups(() => now)
  for (now = 100000; now <= 125000; now += 1000) late.open(undefined, [modal])
  assert.equal(late.open(undefined, [modal]), true)
})

test('E2: the keyboard\'s height lifts a floating panel; the browser\'s bar does not', () => {
  assert.equal(keyboardInset(800, 450, 0), 350)
  assert.equal(keyboardInset(800, 760, 0), 0)
  assert.equal(keyboardInset(800, 450, 350), 0)                        // scrolled, not covered
  assert.match(WIDGET_CSS, /#bbc-widget-root\.bw-kb-open \.bw-panel:not\(\.bw-panel--embedded\) \{\s+bottom: calc\(var\(--bw-bottom\) \+ var\(--bw-kb, 0px\)\);/)
  assert.match(chat, /onFocus=\{keepComposerInView\}/)
})

// ── What follows the yes (A1, A2) and the channel (the client's own contact) ──────────────────

test('A1/A2: the card\'s yes shows its answer and buttons at once; the stored row takes its place', () => {
  assert.match(chat, /const yesOptions = \(o: ProactiveOpening \| null, text: string\): SendOptions \| undefined =>/)
  // Test 1's "Yes, chat now" goes as the wait button it is (form-left.test.mjs); every other yes as before.
  assert.match(chat, /sendMessage\(first, o\.firstButton \? \{ button: o\.firstButton \} : yesOptions\(o, first\)\)/)
  const send = chat.slice(chat.indexOf('const sendMessage = async'), chat.indexOf('const handleSend'))
  assert.ok(send.indexOf('setMessages(prev => [...prev, optimisticMsg, local])') < send.indexOf('await getTurnstileToken()'),
    'the answer is on screen before the conversation is created')
  assert.match(send, /\.\.\.\(opts\?\.button \? \{ button: opts\.button \} : \{\}\),/)
  assert.match(send, /\.\.\.\(opts\?\.priceSeen \? \{ price_seen: opts\.priceSeen \} : \{\}\),/)
  assert.match(chat, /\(\(sending \|\| isStreaming\) && !streamingText && !localPending\)/)
  // The poll and the send's answer agree: whichever brings the stored row first, it is shown once.
  const local = { id: `${LOCAL_AI_PREFIX}1`, role: 'ai', content: 'Got it — …', created_at: 't1' }
  const user = { id: 'sent-1', role: 'user', content: 'Yes, send me fares', created_at: 't0' }
  const { next } = reconcileBatch([user, local], [
    { id: 'u1', role: 'user', content: 'Yes, send me fares', created_at: 't0' },
    { id: 'a1', role: 'ai', content: 'Got it — …', created_at: 't1' },
  ], 0)
  assert.deepEqual(next.map(m => m.id), ['u1', 'a1'])
})

test('a channel opens its field; the contact goes out as the button it was, through the line too', () => {
  const quick = chat.slice(chat.indexOf('const handleQuickReply'), chat.indexOf('const handleKeyDown'))
  assert.ok(quick.indexOf("chip.kind === 'sms' || chip.kind === 'email'") < quick.indexOf('sendMessage(text)'))
  assert.match(quick, /if \(chip\.kind === 'wait'\) return sendOrQueue\(text, \{ button: 'wait' \}\)/)
  assert.match(quick, /sendOrQueue\(text, \{ button: kind, \.\.\.\(beatRef\.current && price \? \{ priceSeen: price \} : \{\}\) \}\)/)
  assert.match(chat, /sendMessage\(next\.text, next\.opts\)/)
  assert.match(chat, /else sendMessage\(msg\.content, msg\.sendOpts\)/)
  // PostHog hears the kind, never the contact.
  assert.match(quick, /posthog\?\.capture\('chat_channel_given', \{ channel: kind, tunnel,/)
  assert.doesNotMatch(quick.slice(quick.indexOf("'chat_channel_given'"), quick.indexOf("'chat_channel_given'") + 120), /contact|channelValue/)
  // The field: a country beside a number, or one email; the price they saw only on the card's tested sentence.
  assert.match(chat, /type=\{channelForm === 'sms' \? 'tel' : 'email'\}/)
  assert.match(chat, /\{channelForm === 'sms' \? w\.phoneLabel : w\.emailLabel\}/)
  // Back from the field, the channels again: the same words as the server's buttons.
  assert.match(chat, /\{ label: w\.sentSms, kind: 'sms' \},\s+\{ label: w\.sentEmail, kind: 'email' \},/)
  assert.match(chat, /\{beatRef\.current && \(\s+<input\s+class="bw-channel__price"/)
})

test('C1/C2: the card\'s own channel, and the pill after "Not now" until ✕ or a tap', () => {
  assert.match(buttons, /class="bw-card__channel" onClick=\{\(\) => onCardChannel\?\.\(cardChannel\.kind\)\}/)
  assert.match(widget, /onCardChannel=\{\(kind\) => \{ if \(proactive\.offer\) openProactive\(\{ \.\.\.proactive\.offer, firstChannel: kind \}\) \}\}/)
  assert.match(hook, /if \(how === 'not_now' && o\.channel && lsGet\(PILL_CLOSED_KEY\) !== '1'\) \{/)
  assert.match(buttons, /\) : pill\?\.channel && count === 0 \? \(/)
  assert.match(widget, /openProactive\(\{ \.\.\.p, shownAt: Date\.now\(\), firstChannel: p\.channel\.kind \}\)/)
  // A channel icon is a bubble or an envelope, never a brand's logo.
  assert.match(buttons, /kind === 'sms' \? <IconMessage size=\{18\} \/> : <IconMail size=\{18\} \/>/)
})

test('B1: one message as a contactless client leaves, asked once per tab, never over a send or a typed field', () => {
  // How the client left goes with the ask (lever 6: tests/before-you-go.test.mjs has the rule itself).
  assert.match(chat, /const onHidden = \(\) => \{ if \(document\.visibilityState === 'hidden'\) beforeYouGoRef\.current\('hidden'\) \}/)
  assert.match(chat, /if \(Date\.now\(\) - lastActivityRef\.current >= IDLE_LEAVING_MS\) beforeYouGoRef\.current\('idle'\)/)
  assert.match(chat, /\/\/ B1: closing the chat without a contact is leaving it; the corner button shows the message\.\s+beforeYouGoRef\.current\('closed'\)/)
  assert.match(chat, /if \(!cid \|\| embedded \|\| contactGivenRef\.current \|\| askingRef\.current\) return/)
  assert.match(chat, /if \(!mayAsk\(rows, why, \{ busy: busyRef\.current, field: fieldRef\.current \}\)\) return/)
  assert.match(chat, /busyRef\.current = sending \|\| isStreaming \|\| !!input\.trim\(\) \|\| agentTyping\.is_typing/)
  assert.match(chat, /fieldRef\.current = !channelForm \? 'none' : channelValue\.trim\(\) \? 'typed' : 'empty'/)
})

test('the conversation keeps the browser\'s language and time zone: a local number is placed by them', () => {
  assert.match(hook, /\.\.\.\(lang \? \{ browser_lang: lang \} : \{\}\), \.\.\.\(tz \? \{ tz \} : \{\}\)/)
  assert.match(widget, /const meta = \{\s+\.\.\.proactiveMetadata\(\),/)
})

test('an unanswered opening keeps what follows it across pages; anything else written there is dropped', () => {
  const o = {
    trigger: 'kayak', message: 'Hi', quickReplies: ['Yes, send me fares', 'Not now'], trip: null, surface: 'row', shownAt: 5,
    confirm: { message: 'Got it', buttons: [{ kind: 'email', label: 'Email' }] }, channel: { kind: 'sms', label: 'Fares by text' },
    buttons: [{ kind: 'sms', label: 'Fares by text' }], lang: 'fr', firstReply: 'x', firstChannel: 'email',
  }
  const kept = keepOpening(o)
  assert.equal(kept.firstReply, undefined)
  assert.equal(kept.firstChannel, undefined)
  const back = readKeptOpening(JSON.parse(JSON.stringify(kept)))
  assert.deepEqual(back.confirm, o.confirm)
  assert.deepEqual(back.channel, o.channel)
  assert.equal(back.surface, 'row')
  assert.equal(back.lang, 'fr')
  // An opening an older widget kept with its C4 side reads back without it.
  assert.equal(readKeptOpening({ ...JSON.parse(JSON.stringify(kept)), c4: 'early' }).c4, undefined)
  const odd = readKeptOpening({ ...kept, confirm: { message: 'x', buttons: [{ kind: 'call', label: 'Call' }] }, channel: { kind: 'wait', label: 'x' }, lang: 'de' })
  assert.equal(odd.confirm, undefined)
  assert.equal(odd.channel, undefined)
  assert.equal(odd.lang, undefined)
  // An opening kept before SMS (20 Nov) loses its WhatsApp channel: nothing offers WhatsApp any more.
  assert.equal(readKeptOpening({ ...kept, channel: { kind: 'whatsapp', label: 'Fares on WhatsApp' } }).channel, undefined)
})

test('reading back up while an answer streams is never pulled down again', () => {
  // Each chunk renewed the log's 700 ms "it scrolls itself" window, and a visitor's own scroll inside it was
  // read as the log's (CodeRabbit #270). A wheel or a finger on the log, or a key that scrolls up, ends it.
  assert.match(chat, /const stop = \(\) => \{ autoScrollUntilRef\.current = 0 \}/)
  assert.match(chat, /el\.addEventListener\('wheel', onWheel, \{ passive: true \}\)/)
  assert.match(chat, /el\.addEventListener\('touchmove', stop, \{ passive: true \}\)/)
  assert.match(chat, /e\.key === 'ArrowUp' \|\| e\.key === 'PageUp' \|\| e\.key === 'Home'/)
  // The log's own smooth scroll keeps the window: an answer that lands mid-way still pulls it down.
  assert.match(chat, /autoScrollUntilRef\.current = smooth \? Date\.now\(\) \+ 700 : 0/)
})

// ── The visitors who decide in seconds (ADR A1–A7, 20 Nov) ─────────────────────────────────────────
// The places below are made up for the tests.

test('the ads card at 2.5 s in view on a computer, as the Kayak card (owner, 23 Nov; it was 4 s); on a phone at the reading moment (25 Nov)', () => {
  assert.equal(ADS_VISIBLE_S, 2.5)
  assert.equal(ADS_VISIBLE_S, KAYAK_VISIBLE_S)
  const computer = { ad: true, destination: true, phone: false, siteSignals: true }
  assert.equal(pickTrigger(page({ ...computer, visibleS: 2.4 })), null)
  assert.equal(pickTrigger(page({ ...computer, visibleS: 2.5 })), 'ads')
  // Still never before the widget watched the page.
  assert.equal(pickTrigger(page({ ...computer, visibleS: 2.5, watchedS: 0.4 })), null)
  // On a phone the 2.5 s card got an answer from 8 of 894 visitors (17-25 Nov): it waits for a read now
  // (tests/reading-moment.test.mjs has the whole rule).
  assert.equal(pickTrigger(page({ ad: true, destination: true, visibleS: 2.5, siteSignals: true })), null)
  assert.equal(pickTrigger(page({ ad: true, destination: true, visibleS: 12, scrolledScreens: 0.25, siteSignals: true })), 'ads')
  // Kayak still comes first on a visit that is both.
  assert.equal(pickTrigger(page({ kayak: true, ad: true, destination: true, visibleS: 2.5 })), 'kayak')
})

test('ADR A5: the ad\'s search is kept as the server keeps it, and may name a place only after "to" or "in"', () => {
  assert.equal(adKeyword('First Class Flights to Lakeside'), 'first class flights to lakeside')
  assert.equal(adKeyword('  cheap   business+class to <b>lakeside</b> '), 'cheap business class to b lakeside b')
  assert.equal(adKeyword('120253513391840723'), null)          // an ad id, not a search
  assert.equal(adKeyword('ab'), null)
  assert.equal(adKeyword('x'.repeat(81)), null)
  assert.equal(adKeyword(undefined), null)
  assert.equal(keywordNamesPlace('first class flights to lakeside'), true)
  assert.equal(keywordNamesPlace('business class in lakeside'), true)
  assert.equal(keywordNamesPlace('lakeside business class'), false)
  assert.equal(keywordNamesPlace('flights to x'), false)
  assert.equal(keywordNamesPlace(null), false)
  // Only an ad visit carries it, and the ads card asks with it; the measurement says one was sent, never which.
  assert.match(hook, /function adKeywordNow\(\): string \| null \{\s+return adVisitNow\(\) \? adKeyword\(getUtm\(\)\.utm_term\) : null\s+\}/)
  assert.match(hook, /\.\.\.\(trigger === 'ads' && trip\?\.keyword \? \{ ad_keyword: true \} : \{\}\),/)
})

test('ADR A5: a "no" about the ad\'s trip as it was is no "no" about the page now (CodeRabbit #272)', () => {
  const ask = hook.slice(hook.indexOf('const ask = async'), hook.indexOf('const evaluate = () => {'))
  // The stale answer gives the reservation back and asks again; only one about the trip of now switches ads off.
  assert.match(ask, /if \(res && !res\.show && res\.arm === TRIGGER_OFF_ARM && trigger === 'ads' && JSON\.stringify\(adsTrip\(\)\) !== askedTrip\) \{[\s\S]*?giveBack\(\)\s+release\(\)\s+return\s+\}\s+if \(res && !res\.show && res\.arm === TRIGGER_OFF_ARM\) \{[\s\S]*?offInPage = offAfter\(offInPage, trigger\)/)
})

test('ADR A2: the moment says when the site asked for the script and how long it took to come', () => {
  assert.match(hook, /\.\.\.scriptTiming\(\),/)
  assert.match(hook, /\.find\(\(r\) => \/-widget\\\.js\(\?:\[\?#\]\|\$\)\/\.test\(r\.name\)\)/)
})

test('ADR A3: the phone card leads with the trip and asks only its question; without both it shows the message', () => {
  assert.match(buttons, /\{card\.headline && card\.cardText \? \([\s\S]*?<span class="bw-nudge__headline">\{card\.headline\}<\/span>\s+<span class="bw-nudge__text">\{card\.cardText\}<\/span>[\s\S]*?\) : \(\s+<span class="bw-nudge__text">\{card\.message\}<\/span>\s+\)\}/)
  assert.match(WIDGET_CSS, /\.bw-nudge--card \.bw-nudge__headline \{/)
  assert.match(hook, /\.\.\.\(res\.headline && res\.card_text \? \{ headline: res\.headline, cardText: res\.card_text \} : \{\}\),/)
  assert.match(hook, /\.\.\.\(surface === 'card' && opening\.headline \? \{ titled: true \} : \{\}\),/)
})

test('ADR A3: the offer keeps the title and its question together, or neither', async () => {
  const realFetch = globalThis.fetch
  const answer = (extra) => async () => new Response(JSON.stringify({
    show: true, arm: 'proactive', message: 'Hi', quick_replies: ['Yes, send me fares', 'Not now'], ...extra,
  }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  const ask = () => fetchProactiveOffer('https://api.test', {
    visitor_id: 'visitor-0123456789', trigger: 'kayak', trip: null, site: 'bbc', device: 'phone',
  }, 3500)
  try {
    globalThis.fetch = answer({ headline: 'Springfield → Lakeside · Business class', card_text: 'Want fares?' })
    const both = await ask()
    assert.equal(both.headline, 'Springfield → Lakeside · Business class')
    assert.equal(both.card_text, 'Want fares?')
    globalThis.fetch = answer({ headline: 'Springfield → Lakeside' })
    const half = await ask()
    assert.equal(half.headline, undefined)
    assert.equal(half.card_text, undefined)
    // The server's longest title fits (CodeRabbit #272: a 171-character one was refused at 140); past its cap, none.
    globalThis.fetch = answer({ headline: 'x'.repeat(171), card_text: 'Want fares?' })
    assert.equal((await ask()).headline, 'x'.repeat(171))
    assert.equal(TITLE_MAX, 240)
    globalThis.fetch = answer({ headline: 'x'.repeat(TITLE_MAX + 1), card_text: 'Want fares?' })
    assert.equal((await ask()).headline, undefined)
  } finally {
    globalThis.fetch = realFetch
  }
})

test('SMS in place of WhatsApp everywhere: the field, its words and the channel kinds (20 Nov)', () => {
  const field = words('en')
  assert.equal(field.phoneLabel, 'Your mobile number')
  assert.equal(field.phoneHint, 'The agent texts the fares to this number.')
  for (const lang of ['en', 'es', 'fr']) {
    for (const [k, v] of Object.entries(words(lang))) assert.doesNotMatch(v, /whats\s*app/i, `${lang}.${k}`)
  }
  // The widget's source offers no WhatsApp: only the site's own contact links (siteSignals) still name it.
  const src = ['ChatWindow.tsx', 'FloatingButtons.tsx', 'FormRow.tsx', 'api.ts', 'keptOpening.ts', 'i18n.ts', 'channel.ts']
  for (const f of src) {
    const code = read(f).replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, '')
    assert.doesNotMatch(code, /whatsapp/i, f)
  }
})

// ── The owner's contact test (22 Nov 2026): the field at once after the yes, or the choice first ────────────────

test('contact test: the offer brings the side and the field its yes opens; anything else is dropped', async () => {
  const realFetch = globalThis.fetch
  let sent = null
  const answer = (extra) => async (url, init) => {
    sent = JSON.parse(init.body)
    return new Response(JSON.stringify({
      show: true, arm: 'proactive', message: 'Hi', quick_replies: ['Yes, send me fares', 'Not now'], ...extra,
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }
  const ask = () => fetchProactiveOffer('https://api.test', {
    visitor_id: 'visitor-0123456789', trigger: 'kayak', trip: null, site: 'bbc', device: 'phone', opens_field: true,
  }, 3500)
  const confirm = (field) => ({ message: 'Got it', buttons: [{ kind: 'sms', label: 'Text message' }], ...(field ? { field } : {}) })
  try {
    globalThis.fetch = answer({ confirm: confirm('sms'), contact_test: 'field' })
    const field = await ask()
    assert.equal(sent.opens_field, true, 'the widget says it opens the field, so it takes part')
    assert.equal(field.confirm.field, 'sms')
    assert.equal(field.contact_test, 'field')
    // The choice side: the side named, no field.
    globalThis.fetch = answer({ confirm: confirm(null), contact_test: 'choice' })
    const choice = await ask()
    assert.equal(choice.confirm.field, undefined)
    assert.equal(choice.contact_test, 'choice')
    // Neither a field nor a side the widget doesn't know.
    globalThis.fetch = answer({ confirm: confirm('whatsapp'), contact_test: 'agent' })
    const odd = await ask()
    assert.equal(odd.confirm.field, undefined)
    assert.equal(odd.contact_test, undefined)
  } finally {
    globalThis.fetch = realFetch
  }
})

test('contact test: the opening keeps its side and its field across pages; anything else is dropped', () => {
  const o = {
    trigger: 'reader', message: 'Hi', quickReplies: ['Get a fare quote', 'Not now'], trip: null, surface: 'panel', shownAt: 5,
    confirm: { message: 'Sure.', buttons: [{ kind: 'sms', label: 'Text message' }], field: 'email' }, contactTest: 'field',
  }
  const back = readKeptOpening(JSON.parse(JSON.stringify(keepOpening(o))))
  assert.deepEqual(back.confirm, o.confirm)
  assert.equal(back.contactTest, 'field')
  const odd = readKeptOpening({ ...keepOpening(o), confirm: { ...o.confirm, field: 'fax' }, contactTest: 'agent' })
  assert.equal(odd.confirm.field, undefined)
  assert.equal(odd.contactTest, undefined)
})

test('contact test: on the field side the yes opens the field at once, the other channel one tap away', () => {
  const send = chat.slice(chat.indexOf('const sendMessage = async'), chat.indexOf('const handleSend'))
  // The field opens with the words that ask for it, after the answer and its buttons are on screen.
  const opened = send.indexOf('if (opts.local.field && !channelForm) openChannel(opts.local.field, true, localId)')
  assert.ok(opened > send.indexOf('setQuickReplies(opts.local.buttons.map('), 'the buttons stay under the field')
  assert.ok(opened < send.indexOf('await getTurnstileToken()'), 'on screen before the conversation is created')
  // An answer that was not the server's after all takes its field with it: the model's turn, a failed send. Only that
  // send's field, while it still is (CodeRabbit #273): Back, or a channel the visitor tapped since, keeps theirs.
  assert.equal(send.split('if (opts?.local?.field) closeAutoField(localId)').length, 3)
  // Never over a field already open: a retried yes leaves the visitor's own field and its address (CodeRabbit #273).
  assert.match(send, /if \(opts\.local\.field && !channelForm\) openChannel\(opts\.local\.field, true, localId\)/)
  assert.match(chat, /const closeAutoField = \(owner: string \| null\) => \{\s+if \(!owner \|\| autoFieldOwnerRef\.current !== owner\) return/)
  assert.match(chat, /if \(!auto\) autoFieldOwnerRef\.current = null\s+else if \(owner !== undefined\) autoFieldOwnerRef\.current = owner/)
  const back = chat.slice(chat.indexOf('const closeChannel = () => {'), chat.indexOf('const closeAutoField'))
  assert.match(back, /autoFieldOwnerRef\.current = null/)
  // A retried send goes as it was, with the answer shown at once and the field it opens (CodeRabbit #273).
  assert.match(send, /\.\.\.\(opts\.local \? \{ local: opts\.local \} : \{\}\) \} \} : \{\}\),/)
  // The side and the field go back at /chat/init.
  assert.match(send, /\.\.\.\(opening\.contactTest \? \{ contact_test: opening\.contactTest \} : \{\}\),/)
  assert.match(send, /\.\.\.\(opening\.confirm\?\.field \? \{ field: opening\.confirm\.field \} : \{\}\),/)
  // Back from a field that opened by itself shows the answer's buttons; from a tapped channel, the two channels.
  assert.match(chat, /setQuickReplies\(q => \(q && q\.length \? q : \[/)
  // In a field that opened by itself, the other channel: the same field side.
  assert.match(chat, /\{autoField && \(\s+<button\s+type="button"\s+class="bw-channel__other"\s+onClick=\{\(\) => openChannel\(channelForm === 'sms' \? 'email' : 'sms', true\)\}/)
  assert.match(chat, /\{channelForm === 'sms' \? w\.otherEmail : w\.otherSms\}/)
  // PostHog hears whether the field opened by itself and the side, never the contact.
  assert.match(chat, /posthog\?\.capture\('chat_channel_opened', \{ channel: kind, tunnel, \.\.\.\(auto \? \{ auto: true \} : \{\}\), \.\.\.fieldTestRef\.current \}\)/)
  assert.match(chat, /\.\.\.\(autoField \? \{ auto: true \} : \{\}\), \.\.\.fieldTestRef\.current \}\)/)
  // The offer says this widget opens the field, from every door that asks for a card.
  assert.equal(hook.split('opens_field: true').length, 3)
  for (const ev of ["capture('proactive_shown', {", "capture('proactive_engaged', {"]) {
    const at = hook.indexOf(ev)
    assert.ok(at > 0 && hook.slice(at, at + 900).includes('contactTestProps('), ev)
  }
})

test('contact test: the other channel is named in every language the widget speaks', () => {
  assert.equal(words('en').otherSms, 'Text me instead')
  assert.equal(words('en').otherEmail, 'Email me instead')
  for (const lang of ['es', 'fr']) {
    assert.ok(words(lang).otherSms && words(lang).otherEmail, lang)
    assert.notEqual(words(lang).otherSms, words('en').otherSms, lang)
  }
  assert.match(WIDGET_CSS, /\.bw-channel__other \{/)
})

// ── 25 Nov 2026, "De ce nu lasă contactul" (the owner: every proposal, for every visitor) ───────────────

test('the row beside a refused form comes once more in a visit whose card brought no contact, and only the row', () => {
  // A card shown this visit, answered or not, no contact left, the second chance unused: open.
  assert.equal(rowAgainOpen('shown', null, false), true)
  assert.equal(rowAgainOpen('shown', '', false), true)
  // Rule 5, the visits it is not for: no card yet (the first moment is theirs as before), the chat opened by the
  // visitor or the note, a moment only asked for, the site's stop; a contact left; the chance asked for, used, or
  // closed by the site's stop.
  for (const done of [null, '', 'chat', 'asked', 'sent', 'contact']) assert.equal(rowAgainOpen(done, null, false), false, String(done))
  assert.equal(rowAgainOpen('shown', null, true), false)
  for (const again of ['asked', 'used', 'sent', 'contact']) assert.equal(rowAgainOpen('shown', again, false), false, again)
  // Only the quote moment on a refused send whose form is still on the page: where the row stands.
  const refused = { sincePressS: 1, sinceFormActivityS: 0, invalid: true, busy: false, row: true }
  assert.equal(rowMoment('quote', refused), true)
  assert.equal(rowMoment('quote', { ...refused, invalid: false }), false)       // the quote card over the form
  assert.equal(rowMoment('quote', { ...refused, row: false }), false)           // no form to stand beside
  assert.equal(rowMoment('quote', null), false)
  for (const t of ['kayak', 'ads', 'social', 'reader', null]) assert.equal(rowMoment(t, refused), false, String(t))
})

test('the second chance is wired: its own reservation, given back as the first is, measured apart, closed by the site', () => {
  // The run decides at its start which it watches for; a visitor with a conversation (allowed false) gets the row too.
  assert.match(hook, /const rowChance = rowAgain && rowAgainNow\(\)/)
  // Test 1's form-only round takes the run when a reason holds (form-left.test.mjs): the row's chance is the rest.
  assert.match(hook, /const rowOnly = rowChance && !formOnly/)
  assert.match(hook, /function rowAgainNow\(\): boolean \{\s+return rowAgainOpen\(ssGet\(DONE_KEY\), ssGet\(ROW_AGAIN_KEY\), ssGet\(CONTACT_LEFT_KEY\) === '1'\)\s+\}/)
  // Its reservation is its own: the visit's moment stays 'shown'. Every answer that gives a moment back gives this one.
  assert.match(hook, /const baseHeld = rowOnly \? ROW_AGAIN_KEY : DONE_KEY\n(?:\s*\/\/[^\n]*\n)*\s+let held = baseHeld\n(?:\s*\/\/[^\n]*\n)*\s+let pending = false\n\s+const giveBack = \(\) => \{\s+pending = false\s+if \(!disposed && ssGet\(held\) === 'asked'\) ssSet\(held, ''\)\s+\}/)
  const ask = hook.slice(hook.indexOf('const ask = async'), hook.indexOf('const evaluate = () => {'))
  // The four answers that give a moment back, the row's new press (round 8), the form gone, test 1's off arm, test 1's
  // answer for a visitor the browser no longer has, and test 1's card whose 24 h another tab took (CodeRabbit #291).
  assert.equal(ask.split('giveBack()').length - 1, 9)
  assert.doesNotMatch(ask, /ssSet\(DONE_KEY, ''\)/)
  // Only the row may come of it: any other answer gives the chance back.
  assert.match(ask, /if \(\(res\.variant === 'row' \|\| \(rowOnly && trigger !== 'form_left'\)\) && !row\) \{\n(?:\s*\/\/[^\n]*\n)*\s+giveBack\(\)\n\s+release\(\)\n\s+return\n\s+\}/)
  // The visit's moment stays 'shown'; any row shown uses the chance, the visit's own row too (the chance is after a card).
  assert.match(ask, /\} else if \(!rowOnly\) ssSet\(DONE_KEY, 'shown'\)\n\s+if \(row\) ssSet\(ROW_AGAIN_KEY, 'used'\)/)
  // Its answer is measured as a new opening's, and its moments apart from the visit's first.
  assert.match(ask, /engagedRef\.current = false\n\s+shownRef\.current = opening/)
  assert.equal(ask.split('...(rowOnly ? { again: true } : {}),').length - 1, 2)
  const evaluate = hook.slice(hook.indexOf('const evaluate = () => {'), hook.indexOf('const pointerEvents = ['))
  assert.match(evaluate, /if \(rowOnly && !rowAgainNow\(\)\) return/)
  assert.match(evaluate, /const trigger = pickTrigger\(state\)\n\s+if \(!trigger\) return\n(?:\s*\/\/[^\n]*\n)*\s+if \(\(rowOnly \|\| formOnly\) && !rowMoment\(trigger, quote\)\) return/)
  assert.match(evaluate, /fired = true\n\s+pending = true\n\s+held = formOnly \? FORM_KEY : baseHeld\n\s+ssSet\(held, 'asked'\)/)
  // The site's stop closes it (they sent the request, or reached out).
  const stopFor = hook.slice(hook.indexOf('const stopFor = (e: SiteEvent) => {'))
  assert.match(stopFor, /if \(!stop\) return\n(?:\s*\/\/[^\n]*\n)*\s+const again = ssGet\(ROW_AGAIN_KEY\)\n\s+if \(!again \|\| again === 'asked'\) ssSet\(ROW_AGAIN_KEY, stop\)/)
  assert.match(hook, /\}, \[allowed, rowAgain, step, apiUrl\]\)/)
})

test('a contact left in the chat closes the second chance; one the server asked for again is no contact', () => {
  // The server kept it: its answer does not ask for that channel again (card_buttons._unreadable does, its button first).
  assert.match(chat, /const channel = opts\?\.button === 'sms' \|\| opts\?\.button === 'email' \? opts\.button : null\n\s+if \(channel && !buttons\.some\(b => b\.kind === channel\)\) contactGiven\(\)/)
  assert.doesNotMatch(chat, /if \(opts\?\.button === 'sms' \|\| opts\?\.button === 'email'\) contactGivenRef\.current = true/)
  assert.match(chat, /const contactGiven = \(\) => \{\n\s+contactGivenRef\.current = true\n\s+markContactLeft\(\)\n\s+\}/)
  // The form's contact, and an email typed into the chat (only the visit's mark: B1 stays the server's to skip).
  assert.match(chat, /useEffect\(\(\) => \{ if \(contactGivenRef\.current\) markContactLeft\(\) \}, \[\]\)/)
  assert.match(chat, /if \(!opts\?\.button && typedEmail\(text\)\) markContactLeft\(\)/)
  assert.equal(typedEmail('Sure: client@example.com.'), true)
  assert.equal(typedEmail('(client@example.com)'), true)
  assert.equal(typedEmail('mine is CLIENT@Example.co.uk, thanks'), true)
  for (const no of ['j***@gmail.com', 'write to me at client at example dot com', 'JFK to CDG on 12/15', '@example.com', '']) {
    assert.equal(typedEmail(no), false, no)
  }
})

test('a contact left while the second chance\'s row was on its way drops the row, measured (CodeRabbit #282, round 7)', () => {
  // A send finishing after the chat closed marks the contact (ChatWindow): the row asked before it never shows.
  const why = hook.slice(hook.indexOf('const momentPassed'), hook.indexOf('const ask = async'))
  assert.match(why, /if \(stopped\) return stopped\n(?:\s*\/\/[^\n]*\n)*\s+if \(rowOnly && ssGet\(CONTACT_LEFT_KEY\) === '1'\) return 'contact_left'/)
  // A passed moment is measured (moment_passed) and dropped before anything shows, and asks nothing again.
  const ask = hook.slice(hook.indexOf('const ask = async'), hook.indexOf('const evaluate = () => {'))
  // Both must be there before their order means anything: a missing one is -1 (CodeRabbit #282, round 8).
  const measured = ask.indexOf('moment_passed: passed')
  const dropped = ask.indexOf('if (disposed || !res || !res.show || !res.message || passed) {')
  assert.ok(measured >= 0, 'the passed moment is measured')
  assert.ok(dropped >= 0, 'and dropped')
  assert.ok(measured < dropped, 'measured before it is dropped')
  assert.equal(givesMomentBack('contact_left', 0), false)
  // Rule 5: the visit's first moment does not read it (with a chat a visitor has none: `allowed` is false).
  assert.doesNotMatch(why, /if \(ssGet\(CONTACT_LEFT_KEY\)/)
})

test('the row\'s second chance goes back for a new press of the form, and only for it (CodeRabbit #282, round 8)', () => {
  // A new press while the row's answer was on its way is a new send: refused too, it is the row's moment.
  assert.equal(givesRowBack('quote_form', true), true)
  // Rule 5: any other drop spends the chance as it spends the visit's moment, and the visit's moment itself keeps its
  // rules (givesMomentBack): one ask a chance, never one a second while the server doesn't answer.
  for (const passed of [null, 'typing', 'hidden', 'chat_opened', 'popup', 'searched', 'sent', 'contact_left', 'left_page']) {
    assert.equal(givesRowBack(passed, true), false, String(passed))
  }
  assert.equal(givesRowBack('quote_form', false), false, 'the visit\'s first moment keeps its word')
  const ask = hook.slice(hook.indexOf('const ask = async'), hook.indexOf('const evaluate = () => {'))
  const back = ask.indexOf('if (givesRowBack(passed, rowOnly)) {')
  const dropped = ask.indexOf('if (disposed || !res || !res.show || !res.message || passed) {')
  assert.ok(back >= 0 && dropped >= 0 && back < dropped, 'given back before a dropped answer spends it')
  assert.match(ask.slice(back, dropped), /giveBack\(\)\s+release\(\)\s+return/)
  // An answer that never came spends the chance: nothing is given back on that path.
  assert.doesNotMatch(ask.slice(dropped, dropped + 160), /giveBack\(\)/)
})

test('a run that ends before its answer is decided gives its reservation back, and a late answer frees nothing (CodeRabbit #282, round 8)', () => {
  const run = hook.slice(hook.indexOf("const baseHeld = rowOnly ? ROW_AGAIN_KEY : DONE_KEY"), hook.indexOf('  }, [allowed, rowAgain, step, apiUrl])'))
  // The reservation is this run's while nothing is decided about its answer.
  assert.match(run, /fired = true\s+pending = true\s+held = [^\n]+\s+ssSet\(held, 'asked'\)/)
  // A late answer (its run ended) gives back nothing: it would free a newer run's 'asked'.
  assert.match(run, /const giveBack = \(\) => \{\s+pending = false\s+if \(!disposed && ssGet\(held\) === 'asked'\) ssSet\(held, ''\)\s+\}/)
  // The run that ends first gives it back, only while it is still its 'asked' (a site stop said meanwhile stays).
  assert.match(run, /return \(\) => \{\s+disposed = true\s+(?:\/\/[^\n]*\n\s+)*if \(pending && ssGet\(held\) === 'asked'\) ssSet\(held, ''\)\s+pending = false/)
  // Decided: shown, or dropped (used as asked): nothing for the cleanup to give back.
  assert.match(run, /pending = false\s+if \(trigger === 'form_left' \|\| afterCard\) \{[\s\S]*?\} else if \(!rowOnly\) ssSet\(DONE_KEY, 'shown'\)/)
  assert.match(run, /if \(disposed \|\| !res \|\| !res\.show \|\| !res\.message \|\| passed\) \{\s+(?:\/\/[^\n]*\n\s+)*pending = false\s+return/)
})

test('the row\'s contact goes into the conversation the visitor already has, once', () => {
  // The opening is only for a visitor with no conversation: without this the contact typed in the row was dropped.
  assert.match(chat, /const rowContactSentRef = useRef<ProactiveOpening \| null>\(savedConvId \? null : proactive \?\? null\)/)
  const effect = chat.slice(chat.indexOf('const rowContactSentRef'), chat.indexOf('// Verify restored session is still active'))
  assert.match(effect, /if \(!o\?\.firstContact \|\| !convId \|\| rowContactSentRef\.current === o\) return\n\s+rowContactSentRef\.current = o/)
  assert.match(effect, /sendOrQueue\(text, \{ button: kind \}\)/)
  assert.match(effect, /posthog\?\.capture\('chat_channel_given', \{ channel: kind, tunnel, where: 'row', again: true, \.\.\.contactTestProps\(o\) \}\)/)
  assert.match(effect, /\}, \[proactive, convId\]\)/)
  // The chat's ✕ and Escape drop the opening, so a chat opened again later never sends it twice.
  assert.equal(widget.split('setOpening(null)').length - 1, 2)
})
