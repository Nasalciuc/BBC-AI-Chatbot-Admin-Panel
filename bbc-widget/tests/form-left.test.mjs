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
  FORM_LEFT_EVERY_MS, FORM_LEFT_TEST_ID, VISITOR_ID_KEY, capOpen, formLeftArm, formLeftCapOpen, formLeftProps,
  noteFormLeftArm, noteFormLeftShown, readArm, readHeldArm, readShownAt, resetFormLeftForTests, stillVisitor, withShown,
} from '../src/formLeftTest.ts'
import { FORM_LEFT_WITHIN_S, TYPING_QUIET_S, formLeftBlocks, pickFormLeft, pickTrigger, readOff } from '../src/triggers.ts'
import { readConfig } from '../src/liveRead.ts'
import { readKeptOpening } from '../src/keptOpening.ts'

const here = dirname(fileURLToPath(import.meta.url))
const src = (f) => readFileSync(join(here, '..', 'src', f), 'utf8')
const T = 1_700_000_000_000
const DAY = 24 * 60 * 60 * 1000

// A browser for formLeftTest.ts in node: localStorage (shared by its tabs) and one tab's sessionStorage, as Maps.
const fakeStore = (m) => ({
  getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => { m.set(k, String(v)) }, removeItem: (k) => { m.delete(k) },
})
function inBrowser(local, session, run) {
  const had = ['localStorage', 'sessionStorage'].map((k) => [k, Object.getOwnPropertyDescriptor(globalThis, k)])
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: fakeStore(local) })
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: fakeStore(session) })
  try { run() } finally {
    for (const [k, d] of had) { if (d) Object.defineProperty(globalThis, k, d); else delete globalThis[k] }
    resetFormLeftForTests()
  }
}

test('the arm is the server’s word on /chat/live: three arms, anything else none, and the test off clears it', () => {
  for (const a of ['off', 'a', 'b']) assert.equal(readArm(a), a)
  for (const bad of ['A', 'on', '', null, undefined, 1, {}]) assert.equal(readArm(bad), null)
  assert.equal(readConfig({ enabled: false, form_left: 'b' }).form_left, 'b')
  assert.equal(readConfig({ enabled: false, later: true, form_left: 'off' }).form_left, 'off')
  assert.equal(readConfig({ enabled: false, form_left: 'x' }).form_left, undefined)
  // The message that takes no branch: an answer without the key (the test off) carries no arm.
  assert.equal(readConfig({ enabled: false }).form_left, undefined)
  inBrowser(new Map([[VISITOR_ID_KEY, 'v1']]), new Map(), () => {
    resetFormLeftForTests()
    noteFormLeftArm('a', 'v1')
    assert.equal(formLeftArm(), 'a')
    assert.deepEqual(formLeftProps(), { experiment: FORM_LEFT_TEST_ID, form_left: 'a' })
    noteFormLeftArm(undefined, 'v1')
    assert.equal(formLeftArm(), null)
    assert.deepEqual(formLeftProps(), {})
  })
  // Kept with the visitor it was asked for; an id changed meanwhile keeps its own (CodeRabbit #291).
  assert.match(src('live.ts'), /if \(res\.ok && ensureVisitorId\(\) === visitorId\) noteFormLeftArm\(cfg\.form_left, visitorId\)/)
  // The same key api.ts keeps the visitor id under.
  assert.match(src('api.ts'), new RegExp(`const VISITOR_KEY = '${VISITOR_ID_KEY}'`))
  // Every PostHog event carries it, as the agent-first test's side.
  assert.match(src('posthog.ts'), /\{ \.\.\.agentTestProps\(\), \.\.\.formLeftProps\(\), \.\.\.properties \}/)
})

test('one card of the test per visitor in 24 h: a second within them does not come, after them it does', () => {
  assert.equal(FORM_LEFT_EVERY_MS, DAY)
  assert.equal(capOpen(null, T), true)
  assert.equal(capOpen(T, T + 1), false)
  assert.equal(capOpen(T, T + DAY - 1), false)
  assert.equal(capOpen(T, T + DAY), true)
  // One in the future (a clock moved back) is no card, and neither is a value that is not a time.
  assert.equal(capOpen(T + 60_000, T), true)
  assert.equal(capOpen(Number.NaN, T), true)
  // Kept by visitor, as {"<visitor id>": ms}: anything else is no card on record.
  assert.deepEqual(readShownAt(JSON.stringify({ v1: T, v2: T + 5 })), { v1: T, v2: T + 5 })
  for (const bad of [null, '', String(T), '[1]', '{"v1":"soon"}', '{oops']) assert.deepEqual(readShownAt(bad), {})
  // One visitor's card moves only their own, and every card still within its 24 h is kept, however many visitors
  // (CodeRabbit #291): twelve in one day, all held.
  assert.deepEqual(withShown({ v1: T }, 'v2', T + 1000), { v1: T, v2: T + 1000 })
  const day = Array.from({ length: 12 }, (_, i) => i).reduce((o, i) => withShown(o, `v${i}`, T + i * 60_000), {})
  assert.equal(Object.keys(day).length, 12)
  for (let i = 0; i < 12; i++) assert.equal(capOpen(day[`v${i}`], T + 11 * 60_000 + 1), false)
  // The message that takes no branch: a card whose 24 h are over goes, and so does one in the future.
  assert.deepEqual(withShown({ old: T - DAY, ahead: T + 5000, v1: T - 1000 }, 'v2', T), { v1: T - 1000, v2: T })
  inBrowser(new Map([[VISITOR_ID_KEY, 'v1']]), new Map(), () => {
    resetFormLeftForTests()
    noteFormLeftShown(T)
    assert.equal(formLeftCapOpen(T + 1000), false)
    assert.equal(formLeftCapOpen(T + DAY + 1), true)
  })
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
  assert.match(hook, /if \(trigger === 'form_left' \|\| afterCard\) \{[\s\S]*?ssSet\(FORM_KEY, 'shown'\)\s+noteFormLeftShown\(Date\.now\(\), askedFor\)/)
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

test('the arm and the 24 h are the visitor’s: another id has neither, in every tab, and gets them back with its id (CodeRabbit #291)', () => {
  assert.deepEqual(readHeldArm('b:v1'), { arm: 'b', visitorId: 'v1' })
  for (const bad of [null, '', 'b', 'x:v1', 'b:']) assert.equal(readHeldArm(bad), null)
  const local = new Map([[VISITOR_ID_KEY, 'v1']])
  const tab1 = new Map()
  inBrowser(local, tab1, () => {
    noteFormLeftArm('b', 'v1')
    noteFormLeftShown(T)
    assert.equal(formLeftArm(), 'b')
    assert.equal(formLeftCapOpen(T + 1000), false)
    // A form for someone else gives the browser another id (Widget.tsx): this tab's arm and 24 h are not theirs.
    local.set(VISITOR_ID_KEY, 'v2')
    assert.equal(formLeftArm(), null)
    assert.deepEqual(formLeftProps(), {})
    assert.equal(formLeftCapOpen(T + 1000), true)
  })
  // Another tab, opened before the change: its own sessionStorage, its page memory gone, the browser's id the new one.
  inBrowser(local, new Map(tab1), () => {
    assert.equal(formLeftArm(), null)
    assert.equal(formLeftCapOpen(T + 1000), true)
    // The message that takes no branch: back to the id they were for, the same arm and the same 24 h.
    local.set(VISITOR_ID_KEY, 'v1')
    assert.equal(formLeftArm(), 'b')
    assert.equal(formLeftCapOpen(T + 1000), false)
  })
  // No visitor id kept (storage refused): no arm, so no card, and nothing written for the 24 h.
  inBrowser(new Map(), new Map(), () => {
    noteFormLeftArm('a', 'v9')
    assert.equal(formLeftArm(), null)
  })
  // Nothing in Widget.tsx to forget: the binding holds in every tab.
  assert.doesNotMatch(src('Widget.tsx'), /forgetFormLeftArm/)
})

test('each visitor keeps their own 24 h: A, then B in another tab, then A again within them, and A is still held (CodeRabbit #291)', () => {
  const local = new Map([[VISITOR_ID_KEY, 'A']])
  inBrowser(local, new Map(), () => {
    noteFormLeftShown(T)
    assert.equal(formLeftCapOpen(T + 1000), false)
  })
  // Another tab: a form for someone else gives the browser B, who sees a card of their own.
  inBrowser(local, new Map(), () => {
    local.set(VISITOR_ID_KEY, 'B')
    assert.equal(formLeftCapOpen(T + 2000), true)
    noteFormLeftShown(T + 2000)
    assert.equal(formLeftCapOpen(T + 3000), false)
  })
  // A new page with A again, within A's 24 h: B's card did not move A's.
  inBrowser(local, new Map(), () => {
    local.set(VISITOR_ID_KEY, 'A')
    assert.equal(formLeftCapOpen(T + 4000), false)
    assert.equal(formLeftCapOpen(T + DAY), true)
    // The message that takes no branch: B's own 24 h run from B's card.
    local.set(VISITOR_ID_KEY, 'B')
    assert.equal(formLeftCapOpen(T + DAY), false)
    assert.equal(formLeftCapOpen(T + 2000 + DAY), true)
  })
})

test('an answer still on its way when the visitor id changes is not shown, nor spent for the new visitor (CodeRabbit #291)', () => {
  const local = new Map([[VISITOR_ID_KEY, 'A']])
  inBrowser(local, new Map(), () => {
    assert.equal(stillVisitor('A'), true)
    local.set(VISITOR_ID_KEY, 'B')
    assert.equal(stillVisitor('A'), false)
    // The card's 24 h go to the visitor it was asked for, not to whoever the browser has.
    noteFormLeftShown(T, 'A')
    assert.equal(formLeftCapOpen(T + 1000), true)
    local.set(VISITOR_ID_KEY, 'A')
    assert.equal(formLeftCapOpen(T + 1000), false)
  })
  // The wiring: asked for one visitor, read again when the answer comes, before its side, its event or its card, and
  // again after a popup's wait.
  assert.match(hook, /const askedFor = ensureVisitorId\(\)\s+const res = await fetchProactiveOffer\(apiUrl, \{\s+visitor_id: askedFor,/)
  const ask = hook.slice(hook.indexOf('const askedFor = ensureVisitorId()'))
  assert.match(ask, /const goneVisitor = \(\): boolean => \{\s+if \(!\(trigger === 'form_left' \|\| afterCard\) \|\| stillVisitor\(askedFor\)\) return false\s+giveBack\(\)\s+release\(\)\s+return true\s+\}\s+if \(goneVisitor\(\)\) return\n(?:\s*\/\/[^\n]*\n)*\s+if \(res\) noteSide\(res\.test\)/)
  assert.match(ask, /await untilPopupCloses\(ignore\)\s+passed = [^\n]+\n\s+if \(goneVisitor\(\)\) return/)
  assert.ok(ask.lastIndexOf('if (goneVisitor()) return') < ask.indexOf("posthog?.capture('proactive_eligible'"))
  assert.equal(ask.slice(0, ask.indexOf("posthog?.capture('proactive_eligible'")).split('if (goneVisitor()) return').length - 1, 2)
})

test('the 24 h are read again at the card: another tab of the visitor that showed one meanwhile keeps this one away (CodeRabbit #291)', () => {
  // Two tabs of one visitor both asked; the first answer shows its card, and the second finds the 24 h taken.
  const local = new Map([[VISITOR_ID_KEY, 'A']])
  inBrowser(local, new Map(), () => {
    assert.equal(formLeftCapOpen(T), true)
  })
  inBrowser(local, new Map(), () => {
    noteFormLeftShown(T, 'A')
  })
  inBrowser(local, new Map(), () => {
    assert.equal(formLeftCapOpen(T + 1500), false)
  })
  // The wiring: right before the display commit, after every way an answer can be dropped, and with nothing awaited
  // between the read and the write of the 24 h.
  const ask = hook.slice(hook.indexOf('const ask = async'), hook.indexOf('const evaluate = () => {'))
  assert.match(ask, /if \(\(trigger === 'form_left' \|\| afterCard\) && !formLeftCapOpen\(\)\) \{(?:\s*\/\/[^\n]*)*\s+giveBack\(\)\s+release\(\)\s+return\s+\}\s+const surface: Surface/)
  const check = ask.indexOf('&& !formLeftCapOpen()) {')
  const commit = ask.indexOf("ssSet(FORM_KEY, 'shown')")
  assert.ok(check > ask.lastIndexOf('if (goneVisitor()) return') && check < commit)
  assert.doesNotMatch(ask.slice(check, commit), /await /)
})
