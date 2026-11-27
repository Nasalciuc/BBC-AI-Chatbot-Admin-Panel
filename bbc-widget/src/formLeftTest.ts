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
/** When each visitor last saw a card of the test (ms), by visitor id: one visitor's card never moves another's. */
export type ShownAt = Record<string, number>
/** The most visitors whose last card the browser keeps (the newest; one older than 24 h holds nothing anyway). */
export const SHOWN_KEPT = 8

let armInPage: HeldArm | null | undefined
let shownInPage: ShownAt = {}

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

/** The visitors' last cards as kept in localStorage (JSON, {"<visitor id>": ms}); anything else is none. */
export function readShownAt(v: string | null): ShownAt {
  const out: ShownAt = {}
  try {
    const o: unknown = v ? JSON.parse(v) : null
    if (!o || typeof o !== 'object' || Array.isArray(o)) return out
    for (const [id, at] of Object.entries(o as Record<string, unknown>)) {
      if (id && typeof at === 'number' && Number.isFinite(at)) out[id] = at
    }
  } catch { /* not ours, or broken: no card on record */ }
  return out
}

/** `shown` with `visitorId`'s card at `atMs`: the others kept as they were, the newest SHOWN_KEPT only. */
export function withShown(shown: ShownAt, visitorId: string, atMs: number): ShownAt {
  const all = { ...shown, [visitorId]: atMs }
  return Object.fromEntries(Object.entries(all).sort((a, b) => b[1] - a[1]).slice(0, SHOWN_KEPT))
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

/** The 24 h since a visitor's last card of the test (`shownAt`, or none) have run out: a time in the future (a clock
 *  moved back) is none. */
export function capOpen(shownAt: number | null | undefined, nowMs: number): boolean {
  if (shownAt === null || shownAt === undefined || !Number.isFinite(shownAt)) return true
  return !(shownAt <= nowMs && nowMs - shownAt < FORM_LEFT_EVERY_MS)
}

/** Whether this visitor may get a card of the test now: their own last card, read from this page and this browser
 *  (another visitor's card is none of theirs). */
export function formLeftCapOpen(nowMs: number = Date.now()): boolean {
  const visitorId = currentVisitor()
  if (!visitorId) return true
  return capOpen(shownInPage[visitorId], nowMs) && capOpen(readShownAt(lsGet(SHOWN_AT_KEY))[visitorId], nowMs)
}

/** A card of the test was shown to `visitorId` (the one it was asked for): none again for them for 24 h. */
export function noteFormLeftShown(atMs: number = Date.now(), visitorId: string | null = currentVisitor()): void {
  if (!visitorId) return
  shownInPage = withShown(shownInPage, visitorId, atMs)
  lsSet(SHOWN_AT_KEY, JSON.stringify(withShown(readShownAt(lsGet(SHOWN_AT_KEY)), visitorId, atMs)))
}

/** Whether `visitorId` (the one an answer was asked for) is still this browser's visitor. */
export function stillVisitor(visitorId: string): boolean {
  return currentVisitor() === visitorId
}

/** For the tests: forget what this page knows. */
export function resetFormLeftForTests(): void {
  armInPage = undefined
  shownInPage = {}
}
