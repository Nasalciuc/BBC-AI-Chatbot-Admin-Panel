// Test 1, step 1 ("Testul 1: plan de implementare", 27 Nov 2026): the quote window left without a send
// (src/quoteWindowRead.ts, src/quoteWindow.ts), why the moments did not start (triggers.ts quietReasons,
// formLeftBlocks) and the page's away mark in the visit registry (visitsRead.ts awayToken). Pure parts directly; the
// wiring is pinned on the source (no DOM in these tests).

import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import {
  CLOSED_BY_VISITOR, GESTURE_MS, closeHow, closedFromRun, closedProps, newRun, runAfterSiteEvent,
} from '../src/quoteWindowRead.ts'
import { TYPING_QUIET_S, formLeftBlocks, quietReasons } from '../src/triggers.ts'
import { VISIT_EVENTS, awayToken, eventToken } from '../src/visitsRead.ts'

const here = dirname(fileURLToPath(import.meta.url))
const src = (f) => readFileSync(join(here, '..', 'src', f), 'utf8')
const T = 1_700_000_000_000

test('how the window went: the visitor’s gesture just before it went, else the page took it', () => {
  for (const kind of ['close_button', 'escape', 'backdrop']) {
    assert.equal(closeHow({ kind, at: T }, T + 200), kind)
    assert.equal(closeHow({ kind, at: T }, T + GESTURE_MS), kind)
  }
  // An old gesture, or none: the window went without the visitor closing it (no moment after it).
  assert.equal(closeHow({ kind: 'close_button', at: T }, T + GESTURE_MS + 1), 'unmounted')
  assert.equal(closeHow(null, T), 'unmounted')
  // A gesture "after" it went (clocks) is not what closed it.
  assert.equal(closeHow({ kind: 'escape', at: T + 10 }, T), 'unmounted')
  assert.deepEqual(CLOSED_BY_VISITOR, ['close_button', 'escape', 'backdrop'])
})

test('a window a send went out of is no window left: no event', () => {
  let run = runAfterSiteEvent(newRun(T), 'quote_form_submitting')
  assert.equal(closedFromRun(run, 'close_button', T + 5000), null)
  run = runAfterSiteEvent(newRun(T), 'quote_form_submitted')
  assert.equal(closedFromRun(run, 'unmounted', T + 5000), null)
  // The browser let the form submit (quoteWindow.ts onSubmit): the same.
  assert.equal(closedFromRun({ ...newRun(T), sent: true }, 'backdrop', T + 1000), null)
})

test('a send that failed went nowhere: the window left is counted, with the refusal', () => {
  const run = runAfterSiteEvent(runAfterSiteEvent(newRun(T), 'quote_form_submitting'), 'quote_form_failed')
  const c = closedFromRun(run, 'close_button', T + 12_340)
  assert.ok(c)
  assert.equal(c.refused, true)
  assert.equal(c.secondsOpen, 12.3)
})

test('the window left says only what happened in it, as yes or no, never what was typed', () => {
  // The message that takes no branch: nothing done in it.
  const plain = closedFromRun(newRun(T), 'close_button', T + 9_000)
  assert.deepEqual(closedProps(plain, 'bbc'), {
    how: 'close_button', source: 'dom', seconds_open: 9, focused: false, typed: false, refused: false, site: 'bbc',
  })
  // Typed: in it, so focused too. A refused send, from the site's word.
  const typed = closedFromRun({ ...newRun(T), typed: true }, 'escape', T + 3_000)
  assert.equal(typed.focused, true)
  assert.equal(typed.typed, true)
  const refused = closedFromRun(runAfterSiteEvent(newRun(T), 'quote_form_invalid'), 'backdrop', T + 3_000)
  assert.equal(refused.refused, true)
  assert.equal(refused.typed, false)
  for (const c of [plain, typed, refused]) {
    for (const v of Object.values(closedProps(c, 'bct'))) assert.ok(['string', 'number', 'boolean'].includes(typeof v))
  }
  // The watch never reads a field's value: it listens to `input`, `focusin`, `invalid` and `submit` only.
  assert.doesNotMatch(src('quoteWindow.ts'), /\.value\b/)
})

test('one window, one event: the run ends when it goes or the page is hidden, and a new run starts only after', () => {
  const w = src('quoteWindow.ts')
  // finish takes the run away before it reports, so a second call for the same window reports nothing.
  assert.match(w, /const finish = \(how: CloseHow, at: number\) => \{\s*const o = open\s*open = null\s*if \(!o\) return/)
  assert.match(w, /if \(!shown\(open\.el\)\) finish\(closeHow\(open\.run\.gesture, now\), now\)\s*return/)
  assert.match(w, /onHidden = \(\) => \{\s*if \(document\.visibilityState !== 'visible' && open\) finish\('page_hidden'/)
  // A quote form in the page itself is no window: only a dialog, a floating box with the ×, or something fixed.
  assert.match(w, /if \(el\.querySelector\(CLOSE\)\) return floating\(el\) \? \{ el, box: el \} : null/)
  assert.match(w, /const CLOSE = '\[data-qd-close\]'/)
})

test('every reason the moments do not start is counted, not only the first', () => {
  assert.deepEqual(
    quietReasons({ hasChat: true, tabUsed: true, pause: true, noteClosed: true, rowOnly: false }),
    ['chat', 'tab_used', 'pause', 'note_closed'],
  )
  // The row's second chance comes over a conversation and a used moment: only the pause and the note hold it.
  assert.deepEqual(quietReasons({ hasChat: true, tabUsed: true, pause: false, noteClosed: false, rowOnly: true }), [])
  assert.deepEqual(quietReasons({ hasChat: true, tabUsed: true, pause: true, noteClosed: false, rowOnly: true }), ['pause'])
  // The page whose moments start: no reason, no event.
  assert.deepEqual(quietReasons({ hasChat: false, tabUsed: false, pause: false, noteClosed: false, rowOnly: false }), [])
})

const left = (over = {}) => ({
  how: 'close_button', pageVisible: true, sitePopup: false, sinceSiteTypingS: null, chatOpen: false, hasChat: false,
  alreadyUsed: false, visitorId: true, ...over,
})

test('after a window left, every reason the form_left moment would not come', () => {
  // The visitor who closed it themselves, nothing else in the way: no reason (the card can reach them).
  for (const how of CLOSED_BY_VISITOR) assert.deepEqual(formLeftBlocks(left({ how })), [])
  assert.deepEqual(formLeftBlocks(left({ how: 'unmounted' })), ['unmounted'])
  assert.deepEqual(formLeftBlocks(left({ how: 'page_hidden', pageVisible: false })), ['page_hidden'])
  assert.deepEqual(
    formLeftBlocks(left({
      pageVisible: false, sitePopup: true, sinceSiteTypingS: 1, chatOpen: true, hasChat: true, alreadyUsed: true,
      visitorId: false,
    })),
    ['not_in_view', 'site_popup', 'typing', 'chat_open', 'chat', 'already_used', 'no_visitor_id'],
  )
  // Typing longer ago than TYPING_QUIET_S holds nothing.
  assert.deepEqual(formLeftBlocks(left({ sinceSiteTypingS: TYPING_QUIET_S })), [])
})

test('the quiet events are measured from the hook, before the first return, and never in the CRM’s iframe', () => {
  const p = src('proactive.ts')
  assert.match(p, /const quiet = quietReasons\(\{[\s\S]*?\}\)\s*if \(quiet\.length\) \{[\s\S]*?return\s*\}/)
  assert.match(p, /if \(!embedded && !pageQuietSent\)/)
  assert.match(p, /posthog\?\.capture\('proactive_quiet', \{ scope: 'form_left', reasons/)
  // Typing in the quote form itself is the visitor leaving it, not a reason to wait.
  assert.match(p, /t\.closest\('#bbc-widget-root'\) \|\| quoteForm\(t\)\) return/)
  const e = src('entry.tsx')
  assert.match(e, /if \(!embedded\) startQuoteWindow\(\)/)
  assert.match(e, /stopVisits\?\.\(\)\s+\/\/[^\n]*\n\s+stopQuoteWindow\(\)/)
  assert.match(src('Widget.tsx'), /restored: keptOpening, embedded,/)
})

test('the registry keeps the window left and the quiet as short words, and when the page went away', () => {
  assert.equal(eventToken('quote_window_closed', { how: 'close_button', seconds_open: 9 }), 'quote_left:close_button')
  assert.equal(eventToken('proactive_quiet', { scope: 'form_left', reasons: ['typing'] }), 'quiet:form_left')
  assert.equal(eventToken('proactive_quiet', { reasons: ['pause'] }), 'quiet')
  assert.ok(VISIT_EVENTS.quote_window_closed && VISIT_EVENTS.proactive_quiet)
  assert.equal(awayToken(T, T + 12_400), 'away:12')
  assert.equal(awayToken(T, T - 5), 'away:0')
  assert.equal(awayToken(T, T + 10 * 86_400_000), 'away:86400')
  // bbc-live's shape (registry.go): what the widget sends must pass it.
  assert.match(awayToken(T, T + 61_000), /^[a-z][a-z_]{0,31}(:[a-z0-9_]{1,24}){0,2}$/)
  const v = src('visits.ts')
  assert.match(v, /events = addEvent\(events, awayToken\(pageStart, now\)\)\s*sendLast\(\)/)
})

test('a full-screen dialog holds its backdrop: a press outside the box with the form is on it (CodeRabbit #291)', () => {
  const w = src('quoteWindow.ts')
  // The dialog wrapper, and the box in it: the one with the ×, else the part that holds the form; a panel-sized dialog
  // is the box itself.
  assert.match(w, /if \(modal\) return \{ el: modal, box: boxOf\(form, modal\) \}/)
  assert.match(w, /if \(!coversScreen\(w\)\) return w/)
  // Outside the window, or inside it but outside the box, by the pointer's place; a click the keyboard made is nowhere.
  assert.match(w, /else if \(!inOpen\(e\.target\) \|\| outside\(e, open\.box\)\) gesture\('backdrop'\)/)
  assert.match(w, /if \(!\(e instanceof MouseEvent\) \|\| \(e\.type === 'click' && e\.detail === 0\)\) return false/)
})

test('a window a send went out of stays sent when the tab comes back to it (CodeRabbit #291)', () => {
  const w = src('quoteWindow.ts')
  // Hidden with a send gone out: the same window, still shown when the tab is back, is the same run's.
  assert.match(w, /if \(how === 'page_hidden' && o\.run\.sent\) sentHidden = o\.el/)
  assert.match(w, /if \(sentHidden && !shown\(sentHidden\)\) sentHidden = null/)
  assert.match(w, /open = \{ \.\.\.w, run: \{ \.\.\.newRun\(now\), sent: w\.el === sentHidden \} \}/)
  // The message that takes no branch: a window that was not sent starts unsent, also the same one shown again.
  assert.deepEqual(newRun(T).sent, false)
  assert.ok(closedFromRun({ ...newRun(T), sent: false }, 'close_button', T + 1000))
  assert.equal(closedFromRun({ ...newRun(T), sent: true }, 'close_button', T + 1000), null)
})
