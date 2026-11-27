/**
 * Test 1 ("Testul 1: plan de implementare", 27 Nov 2026; bbc-chatbot-api app/services/proactive.py, form_left): the
 * card after the site's quote window was closed unsent. The server names this visitor's arm on every answer of
 * POST /chat/live while the test runs ("off", "a" or "b"; nothing while it is off); this keeps it for the visit and puts
 * it, with the test's name, on every PostHog event the widget sends (posthog.ts), so the test reads on the arm from the
 * page's start, for the visitors who never see a card too. No arm: the widget never asks for the moment.
 *
 * And the owner's rule of 27 Nov for how often: one form_left card (or the row after a card) per visitor per 24 h,
 * the time kept in localStorage, on top of once per visit.
 *
 * No imports: the tests run it in node. Storage refused: it lives in this page's memory.
 */

export type FormLeftArm = 'off' | 'a' | 'b'

/** The test's name on the events (the server's FORM_LEFT_TEST_ID). */
export const FORM_LEFT_TEST_ID = 'form_left_2026_11'
/** At most one card of the test per visitor in this long. */
export const FORM_LEFT_EVERY_MS = 24 * 60 * 60 * 1000

const ARM_KEY = 'bbc_form_left_arm'
const SHOWN_AT_KEY = 'bbc_form_left_at'

let armInPage: FormLeftArm | null | undefined
let shownAtInPage: number | null = null

function ssGet(k: string): string | null { try { return sessionStorage.getItem(k) } catch { return null } }
function ssSet(k: string, v: string) { try { sessionStorage.setItem(k, v) } catch { /* this page's memory holds it */ } }
function ssRemove(k: string) { try { sessionStorage.removeItem(k) } catch { /* this page's memory holds it */ } }
function lsGet(k: string): string | null { try { return localStorage.getItem(k) } catch { return null } }
function lsSet(k: string, v: string) { try { localStorage.setItem(k, v) } catch { /* this page's memory holds it */ } }

/** Only the three arms the server names; anything else is no arm. */
export function readArm(v: unknown): FormLeftArm | null {
  return v === 'off' || v === 'a' || v === 'b' ? v : null
}

/** A /chat/live answer came: its `form_left` is this visit's arm; an answer without one means the test is off. */
export function noteFormLeftArm(v: unknown): void {
  const a = readArm(v)
  armInPage = a
  if (a) ssSet(ARM_KEY, a)
  else ssRemove(ARM_KEY)
}

/** The visitor id changed on this page (a form for someone else, Widget.tsx): the arm the last visitor got is not
 *  this one's, so none, on this page's events and for its moment, until /chat/live names theirs (CodeRabbit #291). */
export function forgetFormLeftArm(): void {
  armInPage = null
  ssRemove(ARM_KEY)
}

/** The visit's arm, or null (the test off, or no answer yet on this visit). */
export function formLeftArm(): FormLeftArm | null {
  if (armInPage !== undefined) return armInPage
  return readArm(ssGet(ARM_KEY))
}

/** What every PostHog event carries while the visit has an arm. */
export function formLeftProps(): { experiment?: string; form_left?: FormLeftArm } {
  const a = formLeftArm()
  return a ? { experiment: FORM_LEFT_TEST_ID, form_left: a } : {}
}

/** The 24 h since the last card of the test have run out (or there was none). A value that is not a time is none. */
export function capOpen(shownAt: string | number | null, nowMs: number): boolean {
  const at = Number(shownAt)
  return !(shownAt !== null && shownAt !== '' && Number.isFinite(at) && at <= nowMs && nowMs - at < FORM_LEFT_EVERY_MS)
}

/** Whether this visitor may get a card of the test now (the 24 h, read from this browser and this page). */
export function formLeftCapOpen(nowMs: number = Date.now()): boolean {
  return capOpen(shownAtInPage, nowMs) && capOpen(lsGet(SHOWN_AT_KEY), nowMs)
}

/** A card of the test was shown: none again for 24 h. */
export function noteFormLeftShown(atMs: number = Date.now()): void {
  shownAtInPage = atMs
  lsSet(SHOWN_AT_KEY, String(atMs))
}

/** For the tests: forget what this page knows. */
export function resetFormLeftForTests(): void {
  armInPage = undefined
  shownAtInPage = null
}
