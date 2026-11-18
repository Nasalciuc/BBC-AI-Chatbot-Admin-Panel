/**
 * When the chat may open by itself — four moments, not one timer for everyone (ADR 02 of
 * "Chat proactiv — analiza și dezbaterea", 15 Nov 2026; `ads` 18 Nov):
 *
 *   quote   pressed "Get free quote", is not on /thank-you, and stopped working on the form
 *   kayak   came from Kayak (paid, ≈$30 a visit) and is still here after 5 s in view
 *   ads     came from a Google or Bing ad (a paid click too), is on a page that names a destination,
 *           and is still here after 5 s in view, as kayak
 *   reader  25 s in view and scrolled at least half a screen
 *
 * When two qualify at once the first in that order wins; one moment per visit, a 24 h pause after
 * "Not now" or ✕ (proactive.ts keeps those). The server says whether this visitor's group sees anything.
 *
 * "In view" counts from when the page first showed something (first-contentful-paint), not from when this
 * script started: on 16-18 Nov the page showed ~1 s after it opened (median, phones), yet the Kayak card came
 * ~9 s after the page view instead of 5 s (median; a quarter of them after 11 s) — the count began only when
 * this script (0.5 MB) did, seconds later. Whoever left between the 5th and the 9th second never saw it.
 * inViewBeforeStartMs says how much of the time before the start counts (proactive.ts measures the start:
 * `widget_start_s`). No moment comes before the widget has watched the page for WATCHED_FIRST_S, though: a
 * visitor already typing, or in a quote form opened before the script started on a page without the site's
 * signals (bbc-signals replays what came before; the page alone does not), shows it in that time. Counted
 * from the widget's start, that wait used to be the 5 s themselves.
 *
 * ads (owner, 18 Nov): on 17-18 Nov about 350 visitors a day from Google and Bing ads stayed at least 8 s
 * and got no moment at all (182 from Bing, 168 from Google). 84% of the ad clicks land on a page about one
 * destination (/business-class-flights/…, /flight/city/…), and few of them read it for 25 s with half a
 * screen scrolled. The card says that destination back, as the Kayak card says the search.
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
export type Trigger = 'kayak' | 'ads' | 'quote' | 'reader' | 'exit'
const TRIGGERS: readonly Trigger[] = ['kayak', 'ads', 'quote', 'reader', 'exit']

/** The server's answer for a moment switched off on its own (PROACTIVE_TRIGGERS), with the feature on: the
 *  visit keeps its one moment for the others (proactive.ts), and this one is not asked again in it. Without
 *  this, switching the ads card off would also take the reader from every ad visitor on a destination page:
 *  the ads moment came first and used the visit (CodeRabbit #269). */
export const TRIGGER_OFF_ARM = 'trigger_off'

/** The moments switched off in this visit, as the tab keeps them ("ads,kayak"): each known one once, anything
 *  else dropped. */
export function readOff(kept: string | null): Trigger[] {
  const named = (kept || '').split(',').map(s => s.trim()).filter((t): t is Trigger => TRIGGERS.includes(t as Trigger))
  return [...new Set(named)]
}

/** What the tab keeps after the server switched `trigger` off: each moment once. */
export function offAfter(kept: string | null, trigger: Trigger): string {
  const off = readOff(kept)
  return (off.includes(trigger) ? off : [...off, trigger]).join(',')
}

/** What can open the chat without the form: one of the moments, or `note` — the visitor tapped the 10 s
 *  note beside the button (owner's decision, 16 Nov: from 13 Nov, 0 of the 35 visitors who opened the
 *  chat got past the form the note led to). The note is no moment: no arm and no switch holds it. */
export type OpeningTrigger = Trigger | 'note'

export const KAYAK_VISIBLE_S = 5
/** C4 (ADR-1b, 18 Nov 2026): the Kayak card at 4 s in view for half the visitors, against the 5 s of today,
 *  from C4_START_MS — after #269's 72 hours of measuring — until the server says the early side has had its
 *  300 cards (C4_HOLD_ARM). Never before 3 s: the card would cover a page nobody has read yet. */
export const KAYAK_EARLY_S = 4
export const C4_START_MS = Date.UTC(2026, 10, 21, 13, 49)
/** The server's answer to an early card once the test is over: ask again at the usual moment. */
export const C4_HOLD_ARM = 'c4_hold'
export const ADS_VISIBLE_S = 5
export const READER_VISIBLE_S = 25
export const READER_SCROLL_SCREENS = 0.5
export const QUOTE_IDLE_S = 8
export const QUOTE_INVALID_IDLE_S = 5
/** D1 (ADR-4a): a refused send whose form the widget can put a row beside — the row covers nothing, so it
 *  comes sooner than a card would. 54 of the 64 refused on 17-18 Nov never sent anything after. */
export const QUOTE_ROW_IDLE_S = 2
/** After this long since the last press — or, unpressed, since the last touch — the form is just part of
 *  the page again. */
export const QUOTE_WATCH_S = 180
/** Only a visitor who did something on the page in the last minute (the plan's guard). */
export const ACTIVE_WITHIN_S = 60
/** The widget watches the page this long before any moment, whatever the time before it counted (above). */
export const WATCHED_FIRST_S = 2
/** Someone typing in a field of the site (a search, a form) is left alone this long after a keystroke. */
export const TYPING_QUIET_S = 5
export const PAUSE_AFTER_DISMISS_MS = 24 * 60 * 60 * 1000
/** No answer from the server in this time: nothing is shown (and a tapped note or corner button opens the form).
 *  3.5 s, not 1.5 s (18 Nov): on 16-18 Nov 84 of 990 moments had no answer within 1.5 s while the server
 *  answered in under 0.2 s (p95), and its 17 "errors" were this widget hanging up mid-answer (499). The time
 *  goes on the visitor's connection — a new one, and the browser's CORS check before the POST — not on the
 *  server; api.ts preconnect opens the connection early. An answer that comes after the visitor moved on is
 *  still dropped (proactive.ts, momentPassed). */
export const OFFER_TIMEOUT_MS = 3500
/** The site's word about a refused send this soon after our press is about that press; later, it is a send
 *  of its own (proactive.ts, the refused kinds). */
export const SAME_SEND_MS = 5000

export interface QuoteState {
  /** Seconds since the last press of the form's submit button. */
  sincePressS: number
  /** Seconds since the visitor last typed, clicked or focused inside that form (the press counts). */
  sinceFormActivityS: number
  /** The browser refused the send: a field was flagged (the `invalid` event). */
  invalid: boolean
  /** A send is in flight (the submit button disabled or busy). */
  busy: boolean
  /** The widget knows the form pressed and can put the row beside it (D1). */
  row?: boolean
}

export interface PageState {
  path: string
  /** The visit came from Kayak (utm_source=kayak, first touch of the tab). */
  kayak: boolean
  /** The visit came from a Google or Bing ad (adVisit: the click id or the ads' tags, first touch of the tab). */
  ad: boolean
  /** The page names a destination the card can say back (tripContext.hasContext). Read only on an ad visit:
   *  false on any other. */
  destination: boolean
  /** Seconds the page has been in view since it first showed something (inViewBeforeStartMs, then counted). */
  visibleS: number
  /** Seconds this widget has watched the page (since it started on it). */
  watchedS: number
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
  /** The moments the server switched off in this visit (TRIGGER_OFF_ARM): never picked again in it. */
  off: readonly Trigger[]
  /** Seconds in view before the Kayak card: KAYAK_VISIBLE_S, or KAYAK_EARLY_S on the early side of C4. */
  kayakS?: number
}

/** No card on the thank-you page, the legal pages or the like (the paths the site uses, Nov 2026). */
const BLOCKED = /^\/(?:thank-?you|thanks|legal|terms|privacy|cookie|refund|disclaimer|unsubscribe)(?:[/?#_-]|$)/i

export function blockedPath(path: string): boolean {
  return BLOCKED.test(path || '/')
}

export function pickTrigger(s: PageState): Trigger | null {
  if (blockedPath(s.path)) return null
  if (s.sinceActivityS > ACTIVE_WITHIN_S) return null
  if (s.watchedS < WATCHED_FIRST_S) return null

  // A send in flight holds every moment off for as long as the form (or the site) says it is in flight,
  // also past the quote watch below: the visitor is waiting for the answer, not for us.
  if (s.quote?.busy) return null

  // A moment the server switched off in this visit is never picked again in it: the next one may come.
  const on = (t: Trigger) => !s.off.includes(t)

  // A press of "Get free quote" puts the form first: until it goes quiet nothing else interrupts.
  if (s.quote && s.quote.sincePressS <= QUOTE_WATCH_S) {
    const idle = s.quote.invalid ? (s.quote.row ? QUOTE_ROW_IDLE_S : QUOTE_INVALID_IDLE_S) : QUOTE_IDLE_S
    if (on('quote') && (s.exitIntent || s.quote.sinceFormActivityS >= idle)) return 'quote'
    return null
  }

  const typing = s.sinceSiteTypingS !== null && s.sinceSiteTypingS < TYPING_QUIET_S
  if (typing) return null
  // The quote form being worked on, pressed or not: no card over it (only the quote moment above).
  if (s.sinceQuoteFormS !== null && s.sinceQuoteFormS <= QUOTE_WATCH_S) return null
  if (on('kayak') && s.kayak && s.visibleS >= (s.kayakS ?? KAYAK_VISIBLE_S)) return 'kayak'
  if (on('ads') && s.ad && s.destination && s.visibleS >= ADS_VISIBLE_S) return 'ads'
  if (on('reader') && s.visibleS >= READER_VISIBLE_S && s.scrolledScreens >= READER_SCROLL_SCREENS) return 'reader'
  return null
}

/** The ads' own tags, for a click that came without its id: 7 of the 596 Google ad visits of 16-18 Nov
 *  (every Bing one had its msclkid). */
const AD_SOURCES = new Set(['google', 'bing', 'microsoft'])
const AD_MEDIUMS = new Set(['cpc', 'ppc', 'paid', 'paidsearch', 'paid_search', 'sem'])

/** The visit came from a Google or Bing ad: the click id they add to the address (gclid, msclkid), or their
 *  source with a paid medium. A Kayak visit is Kayak's (its own moment), whatever else its address carries. */
export function adVisit(kept: { gclid?: string; msclkid?: string; utm_source?: string; utm_medium?: string }): boolean {
  const source = (kept.utm_source || '').trim().toLowerCase()
  if (source === 'kayak') return false
  if (kept.gclid || kept.msclkid) return true
  return AD_SOURCES.has(source) && AD_MEDIUMS.has((kept.utm_medium || '').trim().toLowerCase())
}

/** How much of the time before this script started counts as in view, in ms: from the page's first paint
 *  (`paintMs`, ms since the page opened) to `nowMs` (the same clock), when the page is in view now and the
 *  browser saw it hidden at no point since that paint. 0 when the paint is unknown (an older browser) — the
 *  count then starts with the widget, as it did before. */
export function inViewBeforeStartMs(nowMs: number, paintMs: number | null, inView: boolean, hiddenSincePaint: boolean): number {
  if (!inView || hiddenSincePaint || paintMs === null || !Number.isFinite(paintMs) || !Number.isFinite(nowMs)) return 0
  return Math.max(0, nowMs - Math.max(0, paintMs))
}

/** How long the 10 s note beside the button still waits, in ms (0: it may show). Like the moments, it does
 *  not come up over the quote form while the visitor works on it, pressed or not, nor for QUOTE_WATCH_S
 *  after their last touch (owner, 17 Nov): on phones, from 16 Nov 18:00 to 17 Nov 20:00, 141 visitors had
 *  opened the form in their first 12 s and were still in it, unsent, at 12 s, when the note comes there. */
export function noteWaitMs(sinceQuoteFormS: number | null): number {
  if (sinceQuoteFormS === null || sinceQuoteFormS > QUOTE_WATCH_S) return 0
  // Until just past QUOTE_WATCH_S since the last touch: then the form is part of the page again.
  return Math.floor((QUOTE_WATCH_S - Math.max(0, sinceQuoteFormS)) * 1000) + 1
}

/** The 24 h pause after "Not now" or ✕ is still running. A value that is not a time is no pause. */
export function pauseRunning(pauseUntil: string | null, nowMs: number): boolean {
  const until = Number(pauseUntil)
  return Number.isFinite(until) && until > nowMs
}

/** "Not now" closes the card or the panel and never reaches the server (the server's reply lists end
 *  with it: app/services/proactive.py, _REPLIES; in Spanish and French, proactive_i18n.py). */
export function isNotNow(text: string): boolean {
  return /^(?:not now|ahora no|pas maintenant)$/i.test((text || '').trim())
}

/** C4's side for this visitor: 'early' or 'now', stable per visitor id, from C4_START_MS; null before it,
 *  without an id, or once this browser heard the test is over (`over`). */
export function c4Arm(visitorId: string | null, nowMs: number, over = false): 'early' | 'now' | null {
  if (!visitorId || over || !(nowMs >= C4_START_MS)) return null
  // FNV-1a: the same id always lands on the same side.
  let h = 0x811c9dc5
  for (const ch of `c4:${visitorId}`) {
    h ^= ch.charCodeAt(0)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return h % 2 === 0 ? 'early' : 'now'
}

/** What a reply was, for the measurements (never its text): the card's first button, another button, or typed. */
export function replyKind(text: string, replies: string[]): 'button_1' | 'button_2' | 'text' {
  const i = replies.findIndex(r => r.trim().toLowerCase() === (text || '').trim().toLowerCase())
  if (i === 0) return 'button_1'
  if (i > 0) return 'button_2'
  return 'text'
}
