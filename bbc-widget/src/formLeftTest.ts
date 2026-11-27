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
 * Both are the visitor's (CodeRabbit #291): an arm is kept with the visitor id it was named for, a card's time with the
 * visitor it was shown to, and either counts only while that id is this browser's (api.ts VISITOR_KEY). A form for
 * someone else changes the id (Widget.tsx), in every open tab at once: the last visitor's arm and 24 h go with it, and
 * come back if the id does.
 *
 * No imports: the tests run it in node. Storage refused: it lives in this page's memory.
 */

export type FormLeftArm = 'off' | 'a' | 'b'

/** The test's name on the events (the server's FORM_LEFT_TEST_ID). */
export const FORM_LEFT_TEST_ID = 'form_left_2026_11'
/** At most one card of the test per visitor in this long. */
export const FORM_LEFT_EVERY_MS = 24 * 60 * 60 * 1000
/** Where this browser keeps its visitor id (api.ts VISITOR_KEY, the same key). */
export const VISITOR_ID_KEY = 'bbc_visitor_id'

const ARM_KEY = 'bbc_form_left_arm'
const SHOWN_AT_KEY = 'bbc_form_left_at'

/** An arm the server named, with the visitor it named it for. */
export interface HeldArm { arm: FormLeftArm; visitorId: string }
/** A card of the test shown, with the visitor it was shown to. */
export interface Shown { at: number; visitorId: string }

let armInPage: HeldArm | null | undefined
let shownInPage: Shown | null = null

function ssGet(k: string): string | null { try { return sessionStorage.getItem(k) } catch { return null } }
function ssSet(k: string, v: string) { try { sessionStorage.setItem(k, v) } catch { /* this page's memory holds it */ } }
function ssRemove(k: string) { try { sessionStorage.removeItem(k) } catch { /* this page's memory holds it */ } }
function lsGet(k: string): string | null { try { return localStorage.getItem(k) } catch { return null } }
function lsSet(k: string, v: string) { try { localStorage.setItem(k, v) } catch { /* this page's memory holds it */ } }

/** The visitor id this browser keeps now, or null (none, or storage refused: no visitor the test can follow). */
function currentVisitor(): string | null {
  return lsGet(VISITOR_ID_KEY) || null
}

/** Only the three arms the server names; anything else is no arm. */
export function readArm(v: unknown): FormLeftArm | null {
  return v === 'off' || v === 'a' || v === 'b' ? v : null
}

/** "<arm>:<visitor id>" as kept in sessionStorage, or null for anything else. */
export function readHeldArm(v: string | null): HeldArm | null {
  const i = v ? v.indexOf(':') : -1
  if (!v || i < 0) return null
  const arm = readArm(v.slice(0, i))
  const visitorId = v.slice(i + 1)
  return arm && visitorId ? { arm, visitorId } : null
}

/** "<ms>:<visitor id>" as kept in localStorage, or null for anything else. */
export function readShown(v: string | null): Shown | null {
  const i = v ? v.indexOf(':') : -1
  if (!v || i < 0) return null
  const at = Number(v.slice(0, i))
  const visitorId = v.slice(i + 1)
  return v.slice(0, i) !== '' && Number.isFinite(at) && visitorId ? { at, visitorId } : null
}

/** A /chat/live answer for `visitorId` came: its `form_left` is that visitor's arm; an answer without one means the
 *  test is off. */
export function noteFormLeftArm(v: unknown, visitorId: string): void {
  const a = readArm(v)
  armInPage = a && visitorId ? { arm: a, visitorId } : null
  if (armInPage) ssSet(ARM_KEY, `${armInPage.arm}:${visitorId}`)
  else ssRemove(ARM_KEY)
}

/** The visit's arm for the visitor this browser has now, or null (the test off, no answer yet, or the arm another
 *  visitor's). */
export function formLeftArm(): FormLeftArm | null {
  const held = armInPage !== undefined ? armInPage : readHeldArm(ssGet(ARM_KEY))
  return held && held.visitorId === currentVisitor() ? held.arm : null
}

/** What every PostHog event carries while the visit has an arm. */
export function formLeftProps(): { experiment?: string; form_left?: FormLeftArm } {
  const a = formLeftArm()
  return a ? { experiment: FORM_LEFT_TEST_ID, form_left: a } : {}
}

/** The 24 h since `visitorId`'s last card of the test have run out, or there was none: another visitor's card is none
 *  of this one's, and a time in the future (a clock moved back) is none. */
export function capOpen(shown: Shown | null, nowMs: number, visitorId: string | null): boolean {
  if (!shown || !visitorId || shown.visitorId !== visitorId) return true
  return !(shown.at <= nowMs && nowMs - shown.at < FORM_LEFT_EVERY_MS)
}

/** Whether this visitor may get a card of the test now (the 24 h, read from this browser and this page). */
export function formLeftCapOpen(nowMs: number = Date.now()): boolean {
  const visitorId = currentVisitor()
  return capOpen(shownInPage, nowMs, visitorId) && capOpen(readShown(lsGet(SHOWN_AT_KEY)), nowMs, visitorId)
}

/** A card of the test was shown to this visitor: none again for them for 24 h. */
export function noteFormLeftShown(atMs: number = Date.now()): void {
  const visitorId = currentVisitor()
  if (!visitorId) return
  shownInPage = { at: atMs, visitorId }
  lsSet(SHOWN_AT_KEY, `${atMs}:${visitorId}`)
}

/** For the tests: forget what this page knows. */
export function resetFormLeftForTests(): void {
  armInPage = undefined
  shownInPage = null
}
