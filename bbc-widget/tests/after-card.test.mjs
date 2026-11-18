/**
 * What follows the card (18 Nov 2026, "12 probleme Kayak și chat"): the instant answer to its yes and the
 * channel buttons (A1, A2), the client's own WhatsApp number or email (the owner: "spune că îi vom scrie,
 * unde: pe email și WhatsApp"), the card's channel button and the pill after "Not now" (C1, C2), the row
 * beside the refused quote form (D1), one message before a contactless client leaves (B1), popups (E1),
 * the keyboard (E2), the card in Spanish and French (ADR-9b), and the Kayak card's moment test (C4).
 *
 * Pure parts tested directly; the wiring pinned on the source. The server's half is
 * bbc-chatbot-api/tests/test_card_buttons.py. Every contact here is invented.
 */
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import { guessCountry, ownerOf, phoneFor, readablePhone, validEmail, validWhatsapp } from '../src/channel.ts'
import { COUNTRIES } from '../src/countries.ts'
import { channelMessage, widgetLang, words } from '../src/i18n.ts'
import { askBeforeYouGo, fetchProactiveOffer, readButtons, readChannel } from '../src/api.ts'
import {
  C4_HOLD_ARM, C4_START_MS, KAYAK_EARLY_S, KAYAK_VISIBLE_S, QUOTE_INVALID_IDLE_S, QUOTE_ROW_IDLE_S, c4Arm, isNotNow,
  pickTrigger,
} from '../src/triggers.ts'
import { LAYOUT_AFTER_MS, coveredShare, watchPopups } from '../src/overlay.ts'
import { keyboardInset } from '../src/keyboard.ts'
import { keepOpening, readKeptOpening } from '../src/keptOpening.ts'
import { LOCAL_AI_PREFIX, reconcileBatch } from '../src/reconcile.ts'
import { WIDGET_CSS } from '../src/styles.ts'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const read = (f) => readFileSync(join(SRC, f), 'utf8')
const chat = read('ChatWindow.tsx')
const widget = read('Widget.tsx')
const buttons = read('FloatingButtons.tsx')
const hook = read('proactive.ts')
const row = read('FormRow.tsx')

const AU = COUNTRIES.find(c => c.code === 'AU')
const US = COUNTRIES[0]

// ── The client's contact: WhatsApp number or email ─────────────────────────────────────────

test('a number is placed in its country: its own code first, then the country beside the field', () => {
  assert.equal(phoneFor('0412 345 678', AU), '+61412345678')         // #3296: the trunk 0 dropped
  assert.equal(phoneFor('+61 412 345 678', US), '+61412345678')      // its own code wins
  assert.equal(phoneFor('0061 412 345 678', US), '+61412345678')
  assert.equal(phoneFor('(210) 555-0100', US), '+12105550100')
  assert.equal(phoneFor('1 210 555 0100', US), '+12105550100')
  assert.equal(phoneFor('12 34', AU), '')
  assert.equal(validWhatsapp(COUNTRIES, '0412 345 678', AU), '+61412345678')
  assert.equal(validWhatsapp(COUNTRIES, '0412 345', AU), null)       // too short for Australia
  assert.equal(validWhatsapp(COUNTRIES, '210 555 0100', US), '+12105550100')
  assert.equal(readablePhone(COUNTRIES, '+61412345678'), '+61 412 345 678')
  assert.equal(ownerOf(COUNTRIES, '+971501234567').code, 'AE')
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

test('what goes out as the client\'s message is what the agent reads, in the client\'s language', () => {
  assert.equal(channelMessage('en', 'whatsapp', '+61 412 345 678'), 'WhatsApp: +61 412 345 678')
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
    readButtons([{ kind: 'whatsapp', label: 'WhatsApp' }, { kind: 'call', label: 'Call' }, { kind: 'email', label: ' ' },
      { kind: 'wait', label: "I'll wait here" }, 'x', null]),
    [{ kind: 'whatsapp', label: 'WhatsApp' }, { kind: 'wait', label: "I'll wait here" }],
  )
  assert.equal(readChannel({ kind: 'wait', label: 'x' }), null)
  assert.deepEqual(readChannel({ kind: 'email', label: 'Email me fares' }), { kind: 'email', label: 'Email me fares' })
})

test('the offer brings what follows the yes, the card\'s channel, the row\'s buttons and the language', async () => {
  const realFetch = globalThis.fetch
  let sent = null
  globalThis.fetch = async (url, init) => {
    sent = JSON.parse(init.body)
    return new Response(JSON.stringify({
      show: true, arm: 'proactive', message: 'Hola', quick_replies: ['Sí, envíenme tarifas', 'Ahora no'], lang: 'es',
      confirm: { message: 'Perfecto: …', buttons: [{ kind: 'whatsapp', label: 'WhatsApp' }, { kind: 'nope', label: 'x' }] },
      channel: { kind: 'whatsapp', label: 'Tarifas por WhatsApp' }, variant: 'beat',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })
  }
  try {
    const res = await fetchProactiveOffer('https://api.test', {
      visitor_id: 'visitor-0123456789', trigger: 'kayak', trip: null, site: 'bbc', lang: 'es-MX', device: 'phone', c4: 'early',
    }, 3500)
    assert.equal(sent.lang, 'es-MX')
    assert.equal(sent.device, 'phone')
    assert.equal(sent.c4, 'early')
    assert.equal(res.lang, 'es')
    assert.equal(res.variant, 'beat')
    assert.deepEqual(res.confirm, { message: 'Perfecto: …', buttons: [{ kind: 'whatsapp', label: 'WhatsApp' }] })
    assert.deepEqual(res.channel, { kind: 'whatsapp', label: 'Tarifas por WhatsApp' })
  } finally {
    globalThis.fetch = realFetch
  }
})

test('before you go: the server\'s message with its buttons, or nothing', async () => {
  const realFetch = globalThis.fetch
  globalThis.fetch = async () => new Response(JSON.stringify({
    show: true, message: 'Before you go — …', id: 'm9', created_at: '2026-11-18T10:00:00+00:00',
    buttons: [{ kind: 'whatsapp', label: 'WhatsApp' }, { kind: 'email', label: 'Email' }],
  }), { status: 200 })
  try {
    const out = await askBeforeYouGo('https://api.test', 'conv-1')
    assert.equal(out.show, true)
    assert.equal(out.buttons.length, 2)
    globalThis.fetch = async () => new Response(JSON.stringify({ show: false, why: 'has_contact' }), { status: 200 })
    assert.deepEqual(await askBeforeYouGo('https://api.test', 'conv-1'), { show: false })
    globalThis.fetch = async () => { throw new Error('offline') }
    assert.equal(await askBeforeYouGo('https://api.test', 'conv-1'), null)
  } finally {
    globalThis.fetch = realFetch
  }
})

// ── The moments: "Not now" in three languages, C4, the row's sooner moment ───────────────────

const page = (over = {}) => ({
  path: '/', kayak: false, ad: false, destination: false, visibleS: 0, watchedS: 10, sinceActivityS: 1,
  scrolledScreens: 0, phone: true, sinceSiteTypingS: null, quote: null, sinceQuoteFormS: null, exitIntent: false,
  off: [], ...over,
})

test('"Not now" is heard in Spanish and French too, and never reaches the server', () => {
  for (const no of ['Not now', 'Ahora no', 'Pas maintenant', ' pas maintenant ']) assert.equal(isNotNow(no), true, no)
  assert.equal(isNotNow('Ahora no, gracias'), false)
})

test('C4: from its start, half the Kayak visitors get the card at 4 s, the other half at 5 s', () => {
  assert.equal(c4Arm('visitor-1', C4_START_MS - 1), null)
  assert.equal(c4Arm(null, C4_START_MS + 1), null)
  assert.equal(c4Arm('visitor-1', C4_START_MS + 1, true), null)          // this browser heard it is over
  const sides = new Set()
  for (let i = 0; i < 40; i++) sides.add(c4Arm(`visitor-${i}`, C4_START_MS + 1))
  assert.deepEqual([...sides].sort(), ['early', 'now'])
  assert.equal(c4Arm('visitor-7', C4_START_MS + 1), c4Arm('visitor-7', C4_START_MS + 9e9))  // stable
  assert.ok(KAYAK_EARLY_S >= 3 && KAYAK_EARLY_S < KAYAK_VISIBLE_S)
  assert.equal(pickTrigger(page({ kayak: true, visibleS: 4.2, kayakS: KAYAK_EARLY_S })), 'kayak')
  assert.equal(pickTrigger(page({ kayak: true, visibleS: 4.2 })), null)
  assert.equal(C4_HOLD_ARM, 'c4_hold')
  assert.match(hook, /if \(res && !res\.show && res\.arm === C4_HOLD_ARM\) \{[\s\S]*?lsSet\(C4_OVER_KEY, '1'\)[\s\S]*?fired = false/)
})

test('D1: a refused send beside a form the widget sees gets its row sooner than a card', () => {
  const quote = { sincePressS: 2.5, sinceFormActivityS: 2.5, invalid: true, busy: false }
  assert.equal(pickTrigger(page({ quote: { ...quote, row: true } })), 'quote')
  assert.equal(pickTrigger(page({ quote })), null)
  assert.ok(QUOTE_ROW_IDLE_S < QUOTE_INVALID_IDLE_S)
  assert.match(hook, /const rowAt = trigger === 'quote' && press\?\.form\?\.isConnected \? press\.form : null/)
  assert.match(hook, /const surface: Surface = row \? 'row' : phone \? 'card' : 'panel'/)
  assert.match(widget, /return placeFormRow\(\s+form, o,\s+\(kind\) => openProactive\(\{ \.\.\.o, surface: 'panel', firstChannel: kind \}\),/)
  // The row stands under the pressed button, outside the widget, with its own styles; it never says "you made a mistake".
  assert.match(row, /const button = form\.querySelector\('button\[type="submit"\], input\[type="submit"\], button:not\(\[type\]\)'\)/)
  assert.match(row, /at\.insertAdjacentElement\(where, host\)/)
  assert.match(WIDGET_CSS, /#bbc-form-row button \{ all: unset;/)
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
  assert.match(chat, /sendMessage\(first, yesOptions\(o, first\)\)/)
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
  assert.ok(quick.indexOf("chip.kind === 'whatsapp' || chip.kind === 'email'") < quick.indexOf('sendMessage(text)'))
  assert.match(quick, /if \(chip\.kind === 'wait'\) return sendOrQueue\(text, \{ button: 'wait' \}\)/)
  assert.match(quick, /sendOrQueue\(text, \{ button: kind, \.\.\.\(beatRef\.current && price \? \{ priceSeen: price \} : \{\}\) \}\)/)
  assert.match(chat, /sendMessage\(next\.text, next\.opts\)/)
  assert.match(chat, /else sendMessage\(msg\.content, msg\.sendOpts\)/)
  // PostHog hears the kind, never the contact.
  assert.match(quick, /posthog\?\.capture\('chat_channel_given', \{ channel: kind, tunnel,/)
  assert.doesNotMatch(quick.slice(quick.indexOf("'chat_channel_given'"), quick.indexOf("'chat_channel_given'") + 120), /contact|channelValue/)
  // The field: a country beside a number, or one email; the price they saw only on the card's tested sentence.
  assert.match(chat, /type=\{channelForm === 'whatsapp' \? 'tel' : 'email'\}/)
  assert.match(chat, /\{beatRef\.current && \(\s+<input\s+class="bw-channel__price"/)
})

test('C1/C2: the card\'s own channel, and the pill after "Not now" until ✕ or a tap', () => {
  assert.match(buttons, /class="bw-card__channel" onClick=\{\(\) => onCardChannel\?\.\(cardChannel\.kind\)\}/)
  assert.match(widget, /onCardChannel=\{\(kind\) => \{ if \(proactive\.offer\) openProactive\(\{ \.\.\.proactive\.offer, firstChannel: kind \}\) \}\}/)
  assert.match(hook, /if \(how === 'not_now' && o\.channel && lsGet\(PILL_CLOSED_KEY\) !== '1'\) \{/)
  assert.match(buttons, /\) : pill\?\.channel && count === 0 \? \(/)
  assert.match(widget, /openProactive\(\{ \.\.\.p, shownAt: Date\.now\(\), firstChannel: p\.channel\.kind \}\)/)
  // A channel icon is a bubble or an envelope, never a brand's logo.
  assert.match(buttons, /kind === 'whatsapp' \? <IconMessage size=\{18\} \/> : <IconMail size=\{18\} \/>/)
})

test('B1: one message as a contactless client leaves, asked once per tab, never over a send or a field', () => {
  assert.match(chat, /const onHidden = \(\) => \{ if \(document\.visibilityState === 'hidden'\) beforeYouGoRef\.current\(true\) \}/)
  assert.match(chat, /if \(Date\.now\(\) - lastActivityRef\.current >= IDLE_LEAVING_MS\) beforeYouGoRef\.current\(\)/)
  assert.match(chat, /\/\/ B1: closing the chat without a contact is leaving it; the corner button shows the message\.\s+beforeYouGoRef\.current\(true\)/)
  assert.match(chat, /if \(!cid \|\| embedded \|\| contactGivenRef\.current \|\| busyRef\.current\) return/)
  assert.match(chat, /if \(!rows\.some\(m => m\.role === 'user'\) \|\| rows\.some\(m => m\.role === 'agent'\)\) return/)
  assert.match(chat, /busyRef\.current = sending \|\| isStreaming \|\| !!channelForm \|\| !!input\.trim\(\) \|\| agentTyping\.is_typing/)
})

test('the conversation keeps the browser\'s language and time zone: a local number is placed by them', () => {
  assert.match(hook, /\.\.\.\(lang \? \{ browser_lang: lang \} : \{\}\), \.\.\.\(tz \? \{ tz \} : \{\}\)/)
  assert.match(widget, /const meta = \{\s+\.\.\.proactiveMetadata\(\),/)
})

test('an unanswered opening keeps what follows it across pages; anything else written there is dropped', () => {
  const o = {
    trigger: 'kayak', message: 'Hi', quickReplies: ['Yes, send me fares', 'Not now'], trip: null, surface: 'row', shownAt: 5,
    confirm: { message: 'Got it', buttons: [{ kind: 'email', label: 'Email' }] }, channel: { kind: 'whatsapp', label: 'Fares on WhatsApp' },
    buttons: [{ kind: 'whatsapp', label: 'Fares on WhatsApp' }], lang: 'fr', c4: 'early', firstReply: 'x', firstChannel: 'email',
  }
  const kept = keepOpening(o)
  assert.equal(kept.firstReply, undefined)
  assert.equal(kept.firstChannel, undefined)
  const back = readKeptOpening(JSON.parse(JSON.stringify(kept)))
  assert.deepEqual(back.confirm, o.confirm)
  assert.deepEqual(back.channel, o.channel)
  assert.equal(back.surface, 'row')
  assert.equal(back.lang, 'fr')
  assert.equal(back.c4, 'early')
  const odd = readKeptOpening({ ...kept, confirm: { message: 'x', buttons: [{ kind: 'call', label: 'Call' }] }, channel: { kind: 'wait', label: 'x' }, lang: 'de', c4: 'soon' })
  assert.equal(odd.confirm, undefined)
  assert.equal(odd.channel, undefined)
  assert.equal(odd.lang, undefined)
  assert.equal(odd.c4, undefined)
})
