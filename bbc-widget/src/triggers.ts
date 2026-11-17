/**
 * When the chat may open by itself — three moments, not one timer for everyone (ADR 02 of
 * "Chat proactiv — analiza și dezbaterea", 15 Nov 2026):
 *
 *   quote   pressed "Get free quote", is not on /thank-you, and stopped working on the form
 *   kayak   came from Kayak (paid, ≈$30 a visit) and is still here after 5 s in view
 *   reader  25 s in view and scrolled at least half a screen
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
 * The quote form being worked on holds kayak and reader too, pressed or not (owner, 17 Nov): 67 of the
 * 229 Kayak cards on phones (16–17 Nov) came up over a quote form the visitor had opened and not sent
 * yet: only a keystroke in the last 5 s held a moment back. Only the quote moment
 * may follow, after a press, as above. A form opened and never pressed gets no card on that page: in
 * PostHog (13–17 Nov) people who sent the form went quiet in it for up to ~16 s (median of their longest
 * pause), and people who gave up left ~8 s after their last touch, so no wait catches the second without
 * interrupting the first. After QUOTE_WATCH_S without a touch the form is part of the page again.
 *
 * exit (the pointer leaving through the top, on computers) is no longer a moment (owner, 17 Nov): of
 * the 35 panels it opened on 16–17 Nov none got an answer, and 21 of the visitors were gone within
 * 10 s — the panel opened as the tab closed. The pointer leaving still ends a pressed quote form's wait.
 *
 * Pure: proactive.ts measures the page and asks; this decides (node-testable).
 */

/** `exit` is no longer picked (see above); it stays in the type so an opening kept before reads back. */
export type Trigger = 'kayak' | 'quote' | 'reader' | 'exit'

/** What can open the chat without the form: one of the moments, or `note` — the visitor tapped the 10 s
 *  note beside the button (owner's decision, 16 Nov: from 13 Nov, 0 of the 35 visitors who opened the
 *  chat got past the form the note led to). The note is no moment: no arm and no switch holds it. */
export type OpeningTrigger = Trigger | 'note'

export const KAYAK_VISIBLE_S = 5
export const READER_VISIBLE_S = 25
export const READER_SCROLL_SCREENS = 0.5
export const QUOTE_IDLE_S = 8
export const QUOTE_INVALID_IDLE_S = 5
/** After this long since the last press — or, unpressed, since the last touch — the form is just part of
 *  the page again. */
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
  /** A phone-sized screen: the answer is a card above the button, not the panel (proactive.ts). */
  phone: boolean
  /** Seconds since the visitor last typed in a field of the site (not the chat), or null. */
  sinceSiteTypingS: number | null
  quote: QuoteState | null
  /** Seconds since the visitor last worked on the quote form on this page — opened it, focused, typed or
   *  clicked in it, or the site said so (bbc-signals) — pressed or not; null when they never did. */
  sinceQuoteFormS: number | null
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

  // A send in flight holds every moment off for as long as the form (or the site) says it is in flight,
  // also past the quote watch below: the visitor is waiting for the answer, not for us.
  if (s.quote?.busy) return null

  // A press of "Get free quote" puts the form first: until it goes quiet nothing else interrupts.
  if (s.quote && s.quote.sincePressS <= QUOTE_WATCH_S) {
    const idle = s.quote.invalid ? QUOTE_INVALID_IDLE_S : QUOTE_IDLE_S
    if (s.exitIntent || s.quote.sinceFormActivityS >= idle) return 'quote'
    return null
  }

  const typing = s.sinceSiteTypingS !== null && s.sinceSiteTypingS < TYPING_QUIET_S
  if (typing) return null
  // The quote form being worked on, pressed or not: no card over it (only the quote moment above).
  if (s.sinceQuoteFormS !== null && s.sinceQuoteFormS <= QUOTE_WATCH_S) return null
  if (s.kayak && s.visibleS >= KAYAK_VISIBLE_S) return 'kayak'
  if (s.visibleS >= READER_VISIBLE_S && s.scrolledScreens >= READER_SCROLL_SCREENS) return 'reader'
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
