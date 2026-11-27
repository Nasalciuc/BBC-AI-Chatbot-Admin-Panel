// Test 1, step 3 ("Testul 1: plan de implementare", 27 Nov 2026): the form_left moment in the widget. The arm from
// /chat/live on every event (src/formLeftTest.ts, liveRead.ts), one card per visitor per 24 h, the moment itself
// (triggers.ts pickFormLeft) and its wiring in proactive.ts, FloatingButtons.tsx, ChatWindow.tsx (pinned on the source:
// no DOM in these tests). The server's half is bbc-chatbot-api/tests/test_proactive_form_left.py.

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  FORM_LEFT_EVERY_MS, FORM_LEFT_TEST_ID, capOpen, forgetFormLeftArm, formLeftArm, formLeftCapOpen, formLeftProps,
  noteFormLeftArm, noteFormLeftShown, readArm, resetFormLeftForTests,
} from '../src/formLeftTest.ts'
import { FORM_LEFT_WITHIN_S, TYPING_QUIET_S, formLeftBlocks, pickFormLeft, pickTrigger, readOff } from '../src/triggers.ts'
import { readConfig } from '../src/liveRead.ts'
import { readKeptOpening } from '../src/keptOpening.ts'

const here = dirname(fileURLToPath(import.meta.url))
const src = (f) => readFileSync(join(here, '..', 'src', f), 'utf8')
const T = 1_700_000_000_000
const DAY = 24 * 60 * 60 * 1000

test('the arm is the server’s word on /chat/live: three arms, anything else none, and the test off clears it', () => {
  for (const a of ['off', 'a', 'b']) assert.equal(readArm(a), a)
  for (const bad of ['A', 'on', '', null, undefined, 1, {}]) assert.equal(readArm(bad), null)
  assert.equal(readConfig({ enabled: false, form_left: 'b' }).form_left, 'b')
  assert.equal(readConfig({ enabled: false, later: true, form_left: 'off' }).form_left, 'off')
  assert.equal(readConfig({ enabled: false, form_left: 'x' }).form_left, undefined)
  // The message that takes no branch: an answer without the key (the test off) carries no arm.
  assert.equal(readConfig({ enabled: false }).form_left, undefined)
  resetFormLeftForTests()
  noteFormLeftArm('a')
  assert.equal(formLeftArm(), 'a')
  assert.deepEqual(formLeftProps(), { experiment: FORM_LEFT_TEST_ID, form_left: 'a' })
  noteFormLeftArm(undefined)
  assert.equal(formLeftArm(), null)
  assert.deepEqual(formLeftProps(), {})
  // Only for the visitor it was asked for: an id changed meanwhile keeps its own (CodeRabbit #291).
  assert.match(src('live.ts'), /if \(res\.ok && ensureVisitorId\(\) === visitorId\) noteFormLeftArm\(cfg\.form_left\)/)
  // Every PostHog event carries it, as the agent-first test's side.
  assert.match(src('posthog.ts'), /\{ \.\.\.agentTestProps\(\), \.\.\.formLeftProps\(\), \.\.\.properties \}/)
})

test('one card of the test per visitor in 24 h: a second within them does not come, after them it does', () => {
  assert.equal(FORM_LEFT_EVERY_MS, DAY)
  assert.equal(capOpen(null, T), true)
  assert.equal(capOpen(String(T), T + 1), false)
  assert.equal(capOpen(String(T), T + DAY - 1), false)
  assert.equal(capOpen(String(T), T + DAY), true)
  // A value that is not a time, or one in the future (a clock moved back), is no card.
  assert.equal(capOpen('soon', T), true)
  assert.equal(capOpen('', T), true)
  assert.equal(capOpen(String(T + 60_000), T), true)
  resetFormLeftForTests()
  noteFormLeftShown(T)
  assert.equal(formLeftCapOpen(T + 1000), false)
  assert.equal(formLeftCapOpen(T + DAY + 1), true)
  resetFormLeftForTests()
})

const clear = (over = {}) => formLeftBlocks({
  how: 'close_button', pageVisible: true, sitePopup: false, sinceSiteTypingS: null, chatOpen: false, hasChat: false,
  alreadyUsed: false, visitorId: true, ...over,
})

test('the moment comes right after the visitor closed the window, never after the page took it or later', () => {
  assert.equal(FORM_LEFT_WITHIN_S, 2)
  assert.equal(pickFormLeft(0.3, clear()), true)
  assert.equal(pickFormLeft(FORM_LEFT_WITHIN_S, clear({ how: 'escape' })), true)
  assert.equal(pickFormLeft(FORM_LEFT_WITHIN_S + 0.1, clear()), false)
  assert.equal(pickFormLeft(null, clear()), false)
  assert.equal(pickFormLeft(-1, clear()), false)
  assert.equal(pickFormLeft(0.5, clear({ how: 'unmounted' })), false)
  assert.equal(pickFormLeft(0.5, clear({ how: 'page_hidden', pageVisible: false })), false)
  assert.equal(pickFormLeft(0.5, clear({ sinceSiteTypingS: TYPING_QUIET_S - 1 })), false)
  assert.equal(pickFormLeft(0.5, clear({ chatOpen: true })), false)
  assert.equal(pickFormLeft(0.5, clear({ alreadyUsed: true })), false)
})

test('the branch not taken: pickTrigger never names form_left, and every other moment is picked as before', () => {
  const page = {
    path: '/', kayak: true, ad: false, destination: false, visibleS: 30, watchedS: 30, sinceActivityS: 1,
    scrolledScreens: 1, phone: true, sinceSiteTypingS: null, quote: null, sinceQuoteFormS: null, exitIntent: false,
    off: [],
  }
  assert.equal(pickTrigger(page), 'kayak')
  assert.equal(pickTrigger({ ...page, kayak: false }), 'reader')
  // A window just left is quote form work for the other cards: none of them for QUOTE_WATCH_S (pickTrigger's rule).
  assert.equal(pickTrigger({ ...page, sinceQuoteFormS: 1 }), null)
  assert.deepEqual(readOff('form_left,kayak'), ['form_left', 'kayak'])
  // A kept opening of the test reads back, with its buttons.
  const kept = readKeptOpening({
    trigger: 'form_left', message: 'No form needed.', quickReplies: ['Not now'], trip: null, surface: 'panel',
    shownAt: T, variant: 'fl_b_day_quiet', buttons: [{ kind: 'wait', label: 'Yes, chat' }],
  })
  assert.equal(kept?.trigger, 'form_left')
  assert.equal(kept?.variant, 'fl_b_day_quiet')
})

const hook = src('proactive.ts')

test('the form-only round: a used moment, the pause or the closed note leave test 1, a conversation stops it', () => {
  assert.match(hook, /if \(embedded \|\| !allowed \|\| !formLeftOpenNow\(\)\) return\n\s+\}\n\s+const formOnly = quiet\.length > 0\n\s+const rowOnly = rowChance && !formOnly/)
  // Once a visit (its own key) and once in 24 h.
  assert.match(hook, /function formLeftOpenNow\(\): boolean \{\s+return !ssGet\(FORM_KEY\) && formLeftCapOpen\(\)\s+\}/)
  // In that round only the row beside a refused form may come, for a visitor the test runs for, asked as after_card.
  // Read at every tick: after the off arm's "no" (FORM_KEY 'used') or with an answer on its way ('asked'), never again
  // (CodeRabbit #291: the off arm was asked once a second while the row's moment held).
  assert.match(hook, /if \(\(rowOnly \|\| formOnly\) && !rowMoment\(trigger, quote\)\) return\n(?:\s*\/\/[^\n]*\n)*\s+if \(formOnly && \(!formLeftArm\(\) \|\| !formLeftOpenNow\(\)\)\) return/)
  assert.match(hook, /const afterCard = formOnly && trigger === 'quote'/)
  assert.match(hook, /\.\.\.\(afterCard \? \{ after_card: true \} : \{\}\),\n\s+\}, OFFER_TIMEOUT_MS/)
})

test('the moment is read first, holds its own key, and asks with how the window was left', () => {
  const evaluate = hook.slice(hook.indexOf('const evaluate = () => {'))
  // Before the closed note's return, and the site's popup still holds it.
  assert.ok(evaluate.indexOf('formLeftDue(Date.now())') < evaluate.indexOf("if (ssGet(NOTE_CLOSED_KEY) && !formOnly) return"))
  assert.match(evaluate, /if \(leftNow\) \{\s+if \(popups\.open\(\)\) \{/)
  assert.match(evaluate, /held = FORM_KEY\s+ssSet\(held, 'asked'\)\s+void ask\('form_left', matches\(PHONE\), false, leftNow\)/)
  assert.match(hook, /if \(!l \|\| !formLeftArm\(\) \|\| !formLeftOpenNow\(\) \|\| offMoments\(\)\.includes\('form_left'\)\) return null/)
  assert.match(hook, /left: \{ how: leftAsked\.how, source: leftAsked\.source, focused: leftAsked\.focused, typed: leftAsked\.typed \}/)
  // Asked apart from the page's moment: a card shown earlier on the page does not keep it away (88% of those who leave
  // the window had one), one reservation at a time, and no other moment while it is on its way or shown.
  assert.match(evaluate, /const leftNow = !formFired && !pending \? formLeftDue\(Date\.now\(\)\) : null/)
  assert.match(evaluate, /if \(fired \|\| formFired\) return/)
  assert.match(hook, /const release = \(\) => \{\s+if \(trigger === 'form_left'\) formFired = false\s+else fired = false\s+\}/)
  // A window left is heard at once.
  assert.match(hook, /const offLeft = onQuoteWindowClosed\(\(c\) => \{\s+left = c\s+evaluate\(\)\s+\}\)/)
  assert.match(hook, /offSite\(\)\s+offLeft\(\)/)
  // The closed note does not stop test 1 (owner, 27 Nov): its answer is not dropped for it either.
  assert.match(hook, /if \(trigger !== 'form_left' && !formOnly && ssGet\(NOTE_CLOSED_KEY\)\) return 'note_closed'/)
})

test('the off arm is asked and counted, never shown, and the visit keeps its moment for the other cards', () => {
  assert.match(hook, /if \(res && !res\.show && res\.arm === FORM_LEFT_OFF_ARM\) \{[\s\S]*?giveBack\(\)\s+ssSet\(FORM_KEY, 'used'\)\s+release\(\)\s+return\s+\}/)
  // Shown: once a visit and once in 24 h; the visit's moment, if still free, is used too.
  assert.match(hook, /if \(trigger === 'form_left' \|\| afterCard\) \{[\s\S]*?ssSet\(FORM_KEY, 'shown'\)\s+noteFormLeftShown\(\)/)
  // The quiet after a window left says already_used by the same rule the moment reads.
  assert.match(hook, /alreadyUsed: !formLeftOpenNow\(\),/)
})

test('the card shows the server’s buttons; "Yes, chat now" goes as the wait button, Text me and Email me open their field', () => {
  const buttons = src('FloatingButtons.tsx')
  assert.match(buttons, /const cardButtons = card\?\.trigger === 'form_left' && card\.buttons\?\.length \? card\.buttons : null/)
  assert.match(buttons, /onClick=\{\(\) => onCardChannel\?\.\(b\.kind as ChannelKind\)\}/)
  assert.match(buttons, /onClick=\{\(\) => onCardOpen\?\.\(b\.label, 'wait'\)\}/)
  assert.match(buttons, /\{!cardButtons && cardYes && \(/)
  assert.match(src('Widget.tsx'), /firstReply: reply, \.\.\.\(button \? \{ firstButton: button \} : \{\}\)/)
  const chat = src('ChatWindow.tsx')
  assert.match(chat, /sendMessage\(first, o\.firstButton \? \{ button: o\.firstButton \} : yesOptions\(o, first\)\)/)
  assert.match(chat, /if \(o\.trigger === 'form_left' && o\.buttons\?\.length\) \{/)
})

test('with sessionStorage refused, the visit’s guards hold on the page: the off arm’s "no" is not asked again (CodeRabbit #291)', async () => {
  const { visitGet, visitSet, resetVisitGuardForTests } = await import('../src/visitGuard.ts')
  const FORM_KEY = 'bbc_proactive_form'
  const had = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage')
  const use = (s) => Object.defineProperty(globalThis, 'sessionStorage', {
    configurable: true,
    get: () => { if (s === 'refused') throw new Error('SecurityError'); return s },
  })
  try {
    // Refused outright (site data blocked): the off arm's answer writes 'used', and formLeftOpenNow reads it back.
    use('refused')
    resetVisitGuardForTests()
    assert.equal(visitGet(FORM_KEY), null)
    visitSet(FORM_KEY, 'used')
    assert.equal(visitGet(FORM_KEY), 'used')
    // Readable but not writable (a private window's quota): the page's memory still answers.
    const store = new Map()
    use({ getItem: (k) => store.get(k) ?? null, setItem: () => { throw new Error('QuotaExceededError') } })
    resetVisitGuardForTests()
    visitSet(FORM_KEY, 'asked')
    assert.equal(visitGet(FORM_KEY), 'asked')
    // Writable again: the browser keeps it, and the page's memory gives way (an answer freed as '' is free).
    use({ getItem: (k) => store.get(k) ?? null, setItem: (k, v) => { store.set(k, String(v)) } })
    visitSet(FORM_KEY, '')
    assert.equal(store.get(FORM_KEY), '')
    assert.equal(visitGet(FORM_KEY), '')
    // The message that takes no branch: storage that works keeps the guard itself, the page's memory stays empty.
    resetVisitGuardForTests()
    visitSet(FORM_KEY, 'shown')
    assert.equal(store.get(FORM_KEY), 'shown')
    store.delete(FORM_KEY)
    assert.equal(visitGet(FORM_KEY), null)
  } finally {
    if (had) Object.defineProperty(globalThis, 'sessionStorage', had)
    else delete globalThis.sessionStorage
    resetVisitGuardForTests()
  }
  // Every guard of the hook goes through it, the form-only round's among them.
  assert.match(hook, /function ssGet\(k: string\): string \| null \{ return visitGet\(k\) \}/)
  assert.match(hook, /function ssSet\(k: string, v: string\) \{ visitSet\(k, v\) \}/)
  assert.doesNotMatch(hook, /sessionStorage\.(getItem|setItem|removeItem)/)
})

test('a visitor id changed on the page forgets the last visitor’s arm, until /chat/live names the new one’s (CodeRabbit #291)', () => {
  resetFormLeftForTests()
  noteFormLeftArm('b')
  assert.equal(formLeftArm(), 'b')
  forgetFormLeftArm()
  assert.equal(formLeftArm(), null)
  assert.deepEqual(formLeftProps(), {})
  // The new visitor's own answer gives them theirs.
  noteFormLeftArm('a')
  assert.equal(formLeftArm(), 'a')
  // The message that takes no branch: the same visitor keeps the arm the answers named.
  resetFormLeftForTests()
  noteFormLeftArm('off')
  assert.equal(formLeftArm(), 'off')
  resetFormLeftForTests()
  // Forgotten where the id changes (a form for someone else), with the token that proved the old id.
  assert.match(src('Widget.tsx'), /visitorId = makeVisitorId\(\)[\s\S]{0,200}?clearVisitorToken\(\)[^\n]*\n\s+forgetFormLeftArm\(\)/)
})
