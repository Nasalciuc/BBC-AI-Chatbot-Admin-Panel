/**
 * When the chat may open by itself — four moments, not one timer for everyone (ADR 02 of
 * "Chat proactiv — analiza și dezbaterea", 15 Nov 2026):
 *
 *   quote   pressed "Get free quote", is not on /thank-you, and stopped working on the form
 *   kayak   came from Kayak (paid, ≈$30 a visit) and is still here after 5 s in view
 *   reader  25 s in view and scrolled at least half a screen
 *   exit    computers only: the pointer leaves through the top after 15 s
 *
 * When two qualify at once the first in that order wins; one moment per visit, a 24 h pause after
 * "Not now" or ✕ (proactive.ts keeps those). The server says whether this visitor's group sees anything.
 *
 * quote, against the ADR's "no /thank-you within 5 s": in PostHog (12–15 Nov 2026) 128 of the 171
 * sessions that sent the form reached /thank-you more than 6 s after their first press (median 18 s):
 * the press is often the first step of the form, or an error the visitor then fixes. A 5 s timer would
 * have interrupted three of every four people completing it. So the moment is the form going quiet:
 * no typing, clicking or focus in it for 8 s (5 s after the browser flagged a field), or the pointer
 * leaving the page. Never while a send is in flight.
 *
 * Pure: proactive.ts measures the page and asks; this decides (node-testable).
 */

export type Trigger = 'kayak' | 'quote' | 'reader' | 'exit'

/** What can open the chat without the form: one of the moments, or `note` — the visitor tapped the 10 s
 *  note beside the button (owner's decision, 16 Nov: from 13 Nov, 0 of the 35 visitors who opened the
 *  chat got past the form the note led to). The note is no moment: no arm and no switch holds it. */
export type OpeningTrigger = Trigger | 'note'

export const KAYAK_VISIBLE_S = 5
export const READER_VISIBLE_S = 25
export const READER_SCROLL_SCREENS = 0.5
export const EXIT_AFTER_S = 15
export const QUOTE_IDLE_S = 8
export const QUOTE_INVALID_IDLE_S = 5
/** After this long since the last press the form is just part of the page again. */
export const QUOTE_WATCH_S = 180
/** Only a visitor who did something on the page in the last minute (the plan's guard). */
export const ACTIVE_WITHIN_S = 60
/** Someone typing in a field of the site (a search, a form) is left alone this long after a keystroke. */
export const TYPING_QUIET_S = 5
export const PAUSE_AFTER_DISMISS_MS = 24 * 60 * 60 * 1000
/** No answer from the server in this time: nothing is shown. */
export const OFFER_TIMEOUT_MS = 1500

export interface QuoteState {
  /** Seconds since the last press of the form's submit button. */
  sincePressS: number
  /** Seconds since the visitor last typed, clicked or focused inside that form (the press counts). */
  sinceFormActivityS: number
  /** The browser refused the send: a field was flagged (the `invalid` event). */
  invalid: boolean
  /** A send is in flight (the submit button disabled or busy). */
  busy: boolean
}

export interface PageState {
  path: string
  /** The visit came from Kayak (utm_source=kayak, first touch of the tab). */
  kayak: boolean
  /** Seconds the page has been in view since it loaded. */
  visibleS: number
  /** Seconds since the visitor last moved, scrolled, typed or touched the page. */
  sinceActivityS: number
  /** The deepest scroll so far, in screens (scrollY / innerHeight). */
  scrolledScreens: number
  phone: boolean
  finePointer: boolean
  /** Seconds since the visitor last typed in a field of the site (not the chat), or null. */
  sinceSiteTypingS: number | null
  quote: QuoteState | null
  /** The pointer just left the page through the top. */
  exitIntent: boolean
}

/** No card on the thank-you page, the legal pages or the like (the paths the site uses, Nov 2026). */
const BLOCKED = /^\/(?:thank-?you|thanks|legal|terms|privacy|cookie|refund|disclaimer|unsubscribe)(?:[/?#_-]|$)/i

export function blockedPath(path: string): boolean {
  return BLOCKED.test(path || '/')
}

export function pickTrigger(s: PageState): Trigger | null {
  if (blockedPath(s.path)) return null
  if (s.sinceActivityS > ACTIVE_WITHIN_S) return null

  // A press of "Get free quote" puts the form first: until it goes quiet nothing else interrupts.
  if (s.quote && s.quote.sincePressS <= QUOTE_WATCH_S) {
    if (s.quote.busy) return null
    const idle = s.quote.invalid ? QUOTE_INVALID_IDLE_S : QUOTE_IDLE_S
    if (s.exitIntent || s.quote.sinceFormActivityS >= idle) return 'quote'
    return null
  }

  const typing = s.sinceSiteTypingS !== null && s.sinceSiteTypingS < TYPING_QUIET_S
  if (typing) return null
  if (s.kayak && s.visibleS >= KAYAK_VISIBLE_S) return 'kayak'
  if (s.visibleS >= READER_VISIBLE_S && s.scrolledScreens >= READER_SCROLL_SCREENS) return 'reader'
  if (s.exitIntent && !s.phone && s.finePointer && s.visibleS >= EXIT_AFTER_S) return 'exit'
  return null
}

/** The 24 h pause after "Not now" or ✕ is still running. A value that is not a time is no pause. */
export function pauseRunning(pauseUntil: string | null, nowMs: number): boolean {
  const until = Number(pauseUntil)
  return Number.isFinite(until) && until > nowMs
}

/** "Not now" closes the card or the panel and never reaches the server (the server's reply lists end
 *  with it: app/services/proactive.py, _REPLIES). */
export function isNotNow(text: string): boolean {
  return /^not now$/i.test((text || '').trim())
}

/** What a reply was, for the measurements (never its text): the card's first button, another button, or typed. */
export function replyKind(text: string, replies: string[]): 'button_1' | 'button_2' | 'text' {
  const i = replies.findIndex(r => r.trim().toLowerCase() === (text || '').trim().toLowerCase())
  if (i === 0) return 'button_1'
  if (i > 0) return 'button_2'
  return 'text'
}
