/**
 * When the chat may open by itself — four moments, not one timer for everyone (ADR 02 of
 * "Chat proactiv — analiza și dezbaterea", 15 Nov 2026; `ads` 18 Nov):
 *
 *   quote   pressed "Get free quote", is not on /thank-you, and stopped working on the form
 *   kayak   came from Kayak (paid, ≈$30 a visit) and is still here after 2.5 s in view
 *   ads     came from a Google or Bing ad (a paid click too), is on a page that names a destination or
 *           came with a search that names one ("… to Rome"), and is still here after 2.5 s in view
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
 * The visitors who decide in seconds (ADR A1, 20 Nov): on 16-20 Nov the Kayak card came 7.3 s after the page
 * (median, phones) although its moment was 5 s: the widget started 3.4 s after the page showed, then watched
 * WATCHED_FIRST_S more, then waited ~0.6 s for the answer. 76% of the Kayak phone visitors were still there at
 * 4 s, 54% at 7.5 s. Every kayak and ads moment of 18-20 Nov (1,418) had the site's signals, which replay a quote
 * form opened before the widget started: there the first watch is WATCHED_FIRST_SIGNALS_S. The C4 test (4 s
 * against 5 s) is retired: both sides came at ~7 s, so it measured nothing.
 *
 * Ads at 2.5 s, as Kayak (owner, 23 Nov; it was 4 s, for the panel a computer opens over a page not read yet). In the
 * two days from 20 Nov 18:00 UTC 2.7% of the Kayak cards brought a contact and 0.9% of the ads cards, about 390 ad
 * visitors a day: the ads card now asks one yes, as the Kayak card does (the server's words), and comes as soon.
 *
 * The 2.5 s stays (owner, 22 Nov). The card now reaches about 3 in 4 Kayak visits, against about 1 in 3 on 17-18
 * Nov, with the same ~800 visits a day. Its yes grew from 8 taps (19 Nov) to 29 (21 Nov), and the contacts from it
 * from 5 to 17. The chats with no contact grew too, from 3 to 12: visitors who leave in seconds tap yes and go at
 * the contact step. The site's own quote form did not lose any: 2.4-2.9% of Kayak visits sent it on 17-21 Nov.
 * What follows the yes is the owner's contact test (bbc-chatbot-api app/services/contact_field_test.py), not this
 * moment.
 *
 * The reading moment (owner, 25 Nov, "Mai multe chaturi și leaduri", levers 3 and 4): READING_VISIBLE_S in view with
 * READING_SCROLL_SCREENS scrolled, for the visitors who read before they decide. On 17-25 Nov the ads card at 2.5 s got
 * an answer from 0.9% of the phones it came to (8 of 894), on computers 3.5% (43 of 1,235); the reader's card, which
 * comes after a read, 13.9% on phones (22 of 158). Meta's paid visits (Facebook, Instagram: 1,297 to the /lp/ pages)
 * had a moment in 12 of them: the widget did not know them as a paid visit, and 40% open the site's quote form, which
 * holds every card. Half of those who open it do so within 6 s, a quarter after 11 s: at 12 s a card comes to the
 * visitors who are reading, not to the ones heading for the form. So:
 *   social  a paid visit from Meta (socialVisit) gets its own card at the reading moment;
 *   ads     on a phone waits for the reading moment; on a computer it stays at ADS_VISIBLE_S (the panel, 3.5%);
 *   reader  comes at the reading moment for a visit the widget knows no source of (directVisit), a Meta visit and an
 *           ad visit on a phone; at READER_VISIBLE_S with half a screen for every other visit, as before.
 * Kayak stays at 2.5 s: those visitors decide in seconds (ADR A1).
 *
 * Pure: proactive.ts measures the page and asks; this decides (node-testable).
 */

/** `exit` is no longer picked (see above); it stays in the type so an opening kept before reads back. */
export type Trigger = 'kayak' | 'ads' | 'social' | 'quote' | 'reader' | 'exit'
const TRIGGERS: readonly Trigger[] = ['kayak', 'ads', 'social', 'quote', 'reader', 'exit']

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

/** How many times a page asks its moment again after the page named another trip while the answer was on its way
 *  (givesMomentBack): a page whose destination keeps changing is not asked every second, and the server's limit per
 *  address is the chat's too. */
export const TRIP_ASKS_AGAIN = 2

/** Whether an answer that came after its moment passed (proactive.ts, momentPassed) gives the visit its moment back.
 *  Only a trip that changed under it, at most TRIP_ASKS_AGAIN times on a page: the ads card's and the Meta ad's card's
 *  words name the page's old destination and say nothing about the new one, as a "no" about the old trip says nothing
 *  (CodeRabbit #272; #282, round 5). Every other reason (the visitor typing, searching, leaving, the chat opened…)
 *  keeps the moment used. */
export function givesMomentBack(passed: string | null, askedAgain: number): boolean {
  return passed === 'trip_changed' && askedAgain < TRIP_ASKS_AGAIN
}

/** What can open the chat without the form: one of the moments, or `note` — the visitor tapped the 10 s
 *  note beside the button (owner's decision, 16 Nov: from 13 Nov, 0 of the 35 visitors who opened the
 *  chat got past the form the note led to). The note is no moment: no arm and no switch holds it. */
export type OpeningTrigger = Trigger | 'note'

export const KAYAK_VISIBLE_S = 2.5
export const ADS_VISIBLE_S = 2.5
export const READER_VISIBLE_S = 25
export const READER_SCROLL_SCREENS = 0.5
/** The reading moment (above): in view this long, with this much of the page scrolled. */
export const READING_VISIBLE_S = 12
export const READING_SCROLL_SCREENS = 0.25
export const QUOTE_IDLE_S = 8
export const QUOTE_INVALID_IDLE_S = 5
/** D1 (ADR-4a): a refused send whose form the widget can put a row beside — the row covers nothing, so it
 *  comes sooner than a card would. 54 of the 64 refused on 17-18 Nov never sent anything after. At once since
 *  23 Nov (owner; it was 2 s): about 73 visitors a day are refused without a whole contact and 71% of them leave
 *  within 5 s, while the row, after its 2 s and the answer's trip, reached about 40 a day. proactive.ts asks as soon
 *  as the refusal is heard, not at its next second. */
export const QUOTE_ROW_IDLE_S = 0
/** After this long since the last press — or, unpressed, since the last touch — the form is just part of
 *  the page again. */
export const QUOTE_WATCH_S = 180
/** Only a visitor who did something on the page in the last minute (the plan's guard). */
export const ACTIVE_WITHIN_S = 60
/** The widget watches the page this long before any moment, whatever the time before it counted (above). */
export const WATCHED_FIRST_S = 2
/** The same, on a page that sends the site's signals (bbc-signals): what this watch was for — a quote form opened
 *  before the widget started — the site's queue already told (ADR A1). */
export const WATCHED_FIRST_SIGNALS_S = 0.5
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
  /** The visit came from a paid Meta ad: Facebook or Instagram with a paid medium (socialVisit, first touch of the
   *  tab). False on a Kayak or a Google/Bing visit. */
  social?: boolean
  /** The tab's record names no source at all: no campaign tag, no click id, no other site before it (directVisit). */
  direct?: boolean
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
  /** The page sends the site's signals (bbc-signals): the first watch is WATCHED_FIRST_SIGNALS_S. */
  siteSignals?: boolean
}

/** No card on the thank-you page, the legal pages or the like (the paths the site uses, Nov 2026). */
const BLOCKED = /^\/(?:thank-?you|thanks|legal|terms|privacy|cookie|refund|disclaimer|unsubscribe)(?:[/?#_-]|$)/i

export function blockedPath(path: string): boolean {
  return BLOCKED.test(path || '/')
}

export function pickTrigger(s: PageState): Trigger | null {
  if (blockedPath(s.path)) return null
  if (s.sinceActivityS > ACTIVE_WITHIN_S) return null
  if (s.watchedS < (s.siteSignals ? WATCHED_FIRST_SIGNALS_S : WATCHED_FIRST_S)) return null

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
  if (on('kayak') && s.kayak && s.visibleS >= KAYAK_VISIBLE_S) return 'kayak'
  const reading = s.visibleS >= READING_VISIBLE_S && s.scrolledScreens >= READING_SCROLL_SCREENS
  if (on('ads') && s.ad && s.destination && (s.phone ? reading : s.visibleS >= ADS_VISIBLE_S)) return 'ads'
  if (on('social') && s.social && reading) return 'social'
  const read = readsFirst(s) ? reading : s.visibleS >= READER_VISIBLE_S && s.scrolledScreens >= READER_SCROLL_SCREENS
  if (on('reader') && read) return 'reader'
  return null
}

/** The visitors whose reader comes at the reading moment, not after 25 s (above). */
function readsFirst(s: PageState): boolean {
  return !s.kayak && (!!s.direct || !!s.social || (s.ad && s.phone))
}

/** The moment `t` came at the reading moment (READING_VISIBLE_S), not at its own time: what the measurements
 *  compare (proactive_eligible and proactive_shown carry `moment: 'reading'`). */
export function readingMoment(s: PageState, t: Trigger): boolean {
  if (t === 'social') return true
  if (t === 'ads') return s.phone
  if (t === 'reader') return readsFirst(s)
  return false
}

/** Meta's ads (Facebook, Instagram), as its campaigns tag them: utm_source facebook / fb / ig with a paid medium
 *  (paid_social on the /lp/ pages, paid on the others). A fbclid alone is no ad: Facebook adds it to every link
 *  people share, paid or not. */
const SOCIAL_SOURCES = new Set(['facebook', 'fb', 'ig', 'instagram', 'meta'])
const SOCIAL_MEDIUMS = new Set(['paid_social', 'paidsocial', 'paid-social', 'social_paid', 'paid', 'cpc', 'ppc'])

/** The visit came from a paid Meta ad (above). A Kayak visit is Kayak's, a Google or Bing click the ads moment's. */
export function socialVisit(kept: { gclid?: string; msclkid?: string; utm_source?: string; utm_medium?: string }): boolean {
  if (adVisit(kept)) return false
  const source = (kept.utm_source || '').trim().toLowerCase()
  if (source === 'kayak' || !SOCIAL_SOURCES.has(source)) return false
  return SOCIAL_MEDIUMS.has((kept.utm_medium || '').trim().toLowerCase())
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

/** What an ad visitor typed before the click (utm_term), as the server reads it (app/models/trip_context.py,
 *  `_keyword`): lower case, letters, digits, spaces and ' & - only, 3–80 characters with at least three letters;
 *  anything else (an ad id) is no keyword. */
export function adKeyword(term: string | null | undefined): string | null {
  if (typeof term !== 'string') return null
  const s = term.toLowerCase().replace(/[^a-z0-9 '&-]+/g, ' ').replace(/\s+/g, ' ').trim()
  if (s.length < 3 || s.length > 80 || (s.match(/[a-z]/g) || []).length < 3) return null
  return s
}

/** The search may name a place: a word after "to" or "in" ("first class flights to rome"). The server reads the
 *  place itself (proactive.keyword_place) and switches this visit's ads moment off when it finds none; this only
 *  keeps the moment from asking about searches that plainly name none (ADR A5). */
export function keywordNamesPlace(keyword: string | null): boolean {
  return !!keyword && /\b(?:to|in) [a-z]{2,}/.test(keyword)
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

/** A touch, a key or a click that keeps the quote moment waiting after a press: inside the pressed form when
 *  the widget knows it; when it does not (the site's button with no form.qf around it: secret-site's Vue page,
 *  22 Nov), in any field of the site. Without that the moment came 8 s after the press while the visitor was
 *  still typing a name, and the panel that opened took the keys. Never the widget's own fields. */
export function worksOnPressedForm(t: { formKnown: boolean; inForm: boolean; inWidget: boolean; field: boolean }): boolean {
  if (t.inWidget) return false
  return t.formKnown ? t.inForm : t.field
}

/** What a click on the page is to the quote moment: a press of the quote form the widget knows ('press'), work on
 *  the pressed form ('work'), a press of the site's quote button outside a form it knows ('press_unknown'), or
 *  nothing. That button pressed again is a new press, never mere work on the form, though an
 *  <input type="submit"> is a field too (CodeRabbit #276: the second press kept the first one's clock). */
export function quoteClick(c: { knownSubmit: boolean; works: boolean; quoteControl: boolean; inKnownForm: boolean }):
  'press' | 'work' | 'press_unknown' | null {
  if (c.knownSubmit) return 'press'
  if (c.works && !(c.quoteControl && !c.inKnownForm)) return 'work'
  if (c.quoteControl) return 'press_unknown'
  return null
}

/** The visitor's keyboard is in a field of the site (an input, a text area, a select, an editable block), not
 *  the widget's: a chat that opens by itself then leaves the focus there. */
export function keysOnSite(active: Element | null): boolean {
  if (!active || typeof (active as HTMLElement).matches !== 'function') return false
  if (active.closest('#bbc-widget-root')) return false
  return active.matches('input, textarea, select') || (active as HTMLElement).isContentEditable === true
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

/** What a reply was, for the measurements (never its text): the card's first button, another button, or typed. */
export function replyKind(text: string, replies: string[]): 'button_1' | 'button_2' | 'text' {
  const i = replies.findIndex(r => r.trim().toLowerCase() === (text || '').trim().toLowerCase())
  if (i === 0) return 'button_1'
  if (i > 0) return 'button_2'
  return 'text'
}
