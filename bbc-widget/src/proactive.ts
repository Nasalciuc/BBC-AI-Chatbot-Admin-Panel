/**
 * The chat that opens by itself, with the visitor's own search and no form (Dan's approval, 15 Nov 2026).
 *
 * This hook watches the page for the moments of triggers.ts and, at the first one, asks the server
 * (POST /chat/proactive/offer, which writes nothing) whether this visitor sees anything and with what
 * words. On a phone the answer is a card above the corner button; on a computer the chat panel opens
 * in the corner with the words as the assistant's first message. Nothing exists on the server until the
 * visitor answers: ChatWindow then creates the conversation (/chat/init with `trip` and `proactive`).
 *
 * Guards (the plan's "Gărzi comune"): never in the CRM's iframe; never for a visitor who already has a
 * conversation, opened the chat in this tab, or closed the 10 s note with ✕; one moment per visit (the
 * tab's session); 24 h of quiet after "Not now" or ✕; only with the page in view. One exception since 25 Nov
 * (triggers.ts rowAgainOpen): in a visit whose card brought no contact, the row beside a refused quote form may
 * come once more, also over the conversation that card's chat left.
 *
 * Measurements (PostHog, no personal data): proactive_eligible (any group, also with the feature off —
 * the shadow mode; `moment_passed` says why an answer that came after the visitor moved on was not
 * shown; `offer_ms` and `offer_fail` how the answer came; `widget_start_s` how long after the page showed
 * this script started), proactive_shown, proactive_engaged, proactive_dismissed,
 * proactive_note_unanswered when a tap on the note or the corner button got no answer in time (the
 * visitor got the form), and proactive_quote_unrecognized when a "Get free quote" button sits outside a
 * form we recognise (the site's HTML changed: the quote moment would otherwise die silently).
 */
import { useEffect, useRef, useState } from 'preact/hooks'
import { alexWaitLeftMs, holdAlex, noteAlexAnswer, noteAlexShown, noteSide } from './agentTest'
import {
  ensureVisitorId, fetchProactiveOffer, getVisitorId, offerMeasures, type ContactTestSide, type OfferConfirm,
  type OfferTiming,
} from './api'
import { rowField, type ChannelKind, type ChatButton, type RowField } from './channel'
import brand from './config'
import { formLeftArm, formLeftCapOpen, noteFormLeftShown, stillVisitor } from './formLeftTest'
import { browserLanguage, timeZone } from './i18n'
import { keepOpening, readKeptOpening } from './keptOpening'
import { cardAsideWatch, watchPopups } from './overlay'
import { posthog, posthogSessionEntry } from './posthog'
import { onQuoteWindowClosed, quoteForm } from './quoteWindow'
import type { QuoteWindowClosed } from './quoteWindowRead'
import {
  applySiteEvent, fieldKinds, isQuoteFormWork, kindOfField, lastSearch, noMomentPage, onSiteEvent, siteEvents, sitePage,
  withKinds, type FieldKind, type SiteEvent,
} from './siteSignals'
import { hasContext, pickTrip, tripFromFields, tripFromTitle, tripFromUrl, type TripPayload } from './tripContext'
import {
  OFFER_TIMEOUT_MS, PAUSE_AFTER_DISMISS_MS, SAME_SEND_MS, TRIGGER_OFF_ARM,
  adKeyword, adVisit, blockedPath, formLeftBlocks, givesMomentBack, pickFormLeft, givesRowBack, inViewBeforeStartMs, keywordNamesPlace,
  offAfter, pauseRunning, pickTrigger, quietReasons, readOff, rowAgainOpen, rowMoment,
  quoteClick, readingMoment, replyKind, socialVisit, worksOnPressedForm, type OpeningTrigger, type PageState,
  type QuoteState, type Trigger,
} from './triggers'
import { getUtm, namesWhereFrom, sourceToAdd } from './utm'
import { isReturning } from './visitCount'
import { visitGet, visitSet } from './visitGuard'

/** Where the opening shows: a card above the button (phones), the panel (computers), or the row beside the
 *  site's refused quote form (D1, ADR-4a). */
export type Surface = 'card' | 'panel' | 'row'

/** What the chat opened by the card (or by the tapped note) starts with. */
export interface ProactiveOpening {
  trigger: OpeningTrigger
  /** The assistant's first message, composed by the server from the page's trip. */
  message: string
  quickReplies: string[]
  trip: TripPayload | null
  surface: Surface
  shownAt: number
  /** The button the visitor tapped on the phone card: it goes out as their first message. */
  firstReply?: string
  /** That button's kind when the server named one (test 1's "Yes, chat now": `wait`, answered as "I'll wait here"). */
  firstButton?: 'wait'
  /** Which way the server asked the first question (the note's bandit, app/services/note_bandit.py):
   *  sent back with the answer so the conversation starts with the same words and is counted for it. */
  variant?: string
  /** A2: the instant answer to the card's yes, when the card knew the whole trip (the server's words). */
  confirm?: OfferConfirm
  /** C1/C2: the card's own channel button (SMS on a phone, email on a computer). */
  channel?: ChatButton & { kind: ChannelKind }
  /** D1: the row's two buttons beside the refused form. */
  buttons?: ChatButton[]
  /** The language the server wrote the words in ("es", "fr"); sent back so the chat starts in it. */
  lang?: string
  /** ADR A3 (20 Nov): the phone card's own face — the trip as its title and only the opening's question under
   *  it; the chat still opens with `message`. Absent: the card shows `message`, as before. */
  headline?: string
  cardText?: string
  /** The channel the client picked before the chat opened (the card's channel button, the pill): the chat opens
   *  on its field, and the conversation starts with what they leave there. */
  firstChannel?: ChannelKind
  /** The contact the client left in the row beside the refused form (23 Nov): the chat opens with it sent as their
   *  first message ("Email: …"). Never kept with the opening (keptOpening.ts). */
  firstContact?: { kind: ChannelKind; text: string }
  /** D1: the contact the row asks for (channel.ts rowField): the one the form already had, or either. */
  rowField?: RowField
  /** The owner's contact test (22 Nov): this visitor's side as the server named it, sent back at /chat/init and
   *  on the card's events. On the field side `confirm.field` is the field the yes opens at once. */
  contactTest?: ContactTestSide
}

/** The contact test's side on a PostHog event, when the card named one. */
export function contactTestProps(o: Pick<ProactiveOpening, 'contactTest'> | null | undefined): { contact_test?: ContactTestSide } {
  return o?.contactTest ? { contact_test: o.contactTest } : {}
}

// The arm an offer answer names while the owner's agent-first test keeps Alex quiet (agent_first_test.HELD_ARM).
const AGENT_FIRST_ARM = 'agent_first'

// sessionStorage: this visit's moment was used ('asked', 'shown', or 'chat' when the visitor opened the
// chat themselves first).
const DONE_KEY = 'bbc_proactive_done'
// sessionStorage: the row's second chance in a visit whose card brought no contact (triggers.ts rowAgainOpen): '' until
// it is asked for, then 'asked', 'used' once the row showed, or the site's stop ('sent', 'contact').
const ROW_AGAIN_KEY = 'bbc_proactive_row_again'
// sessionStorage: the visitor left a contact in the chat this visit (ChatWindow: one the server kept, an email they
// typed, or the form's): the row's second chance is not for them.
const CONTACT_LEFT_KEY = 'bbc_contact_left'
// sessionStorage: test 1's form-only moments this visit (formLeftTest.ts): '' until asked, then 'asked', 'shown', or 'used'
// (asked and answered no: the off arm, or dropped). Once a visit, whatever the round.
const FORM_KEY = 'bbc_proactive_form'
// The server's answer for test 1's off arm (proactive.FORM_LEFT_OFF): asked and counted, never shown.
const FORM_LEFT_OFF_ARM = 'form_left_off'

/** Test 1 may still bring its card in this visit: not asked yet this visit, none in the last 24 h. */
function formLeftOpenNow(): boolean {
  return !ssGet(FORM_KEY) && formLeftCapOpen()
}

// sessionStorage: the moments the server switched off in this visit (triggers.ts, TRIGGER_OFF_ARM), "ads,kayak".
const OFF_KEY = 'bbc_proactive_off'
// The same, in this page's memory: with the tab's storage refused the list still holds here, or the switched-off
// moment would be asked again every second (CodeRabbit #269). The storage carries it to the next pages.
let offInPage = ''

/** The moments switched off in this visit: what this page heard, and what the tab kept from earlier pages. */
function offMoments(): Trigger[] {
  return readOff(`${ssGet(OFF_KEY) || ''},${offInPage}`)
}
// localStorage: no card until this time (24 h after "Not now" or ✕).
const PAUSE_KEY = 'bbc_proactive_pause_until'
// sessionStorage: the 10 s note beside the button was closed with ✕ in this tab.
const NOTE_CLOSED_KEY = 'bbc_note_closed'
// localStorage: the conversation the card opened (it has no form behind it, so no visitor key).
const CONV_KEY = 'bbc_conv_proactive'
// sessionStorage: Widget.tsx's once-per-visit note; set when a card shows so the note never follows it.
const ATTENTION_KEY = 'bbc_attention_shown'
// sessionStorage: the pill after "Not now" (C2), kept for this visit's pages; localStorage: closed with ✕ for good.
const PILL_KEY = 'bbc_pill'
const PILL_CLOSED_KEY = 'bbc_pill_closed'

const PHONE = '(max-width: 767px)'
const QUOTE_TEXT = /get (?:a )?free quote/i

// The visit's guards: sessionStorage, with this page's memory beneath it when the browser refuses storage
// (visitGuard.ts), so a guard written here holds for the rest of the page (CodeRabbit #291).
function ssGet(k: string): string | null { return visitGet(k) }
function ssSet(k: string, v: string) { visitSet(k, v) }
function lsGet(k: string): string | null { try { return localStorage.getItem(k) } catch { return null } }
function lsSet(k: string, v: string) { try { localStorage.setItem(k, v) } catch { /* storage off */ } }
function lsRemove(k: string) { try { localStorage.removeItem(k) } catch { /* storage off */ } }
function matches(q: string): boolean { try { return window.matchMedia(q).matches } catch { return false } }

/** The cached conversation is one the card opened (it counts as this visitor's session, form or not). */
export function isProactiveConversation(convId: string | null): boolean {
  return !!convId && lsGet(CONV_KEY) === convId
}
export function markProactiveConversation(convId: string): void { lsSet(CONV_KEY, convId) }
/** The card's chat became a conversation (the visitor answered): closing it is no dismissal. */
export function proactiveChatStarted(): boolean { return isProactiveConversation(lsGet('bbc_conv_id')) }
export function forgetProactiveConversation(): void { lsRemove(CONV_KEY) }

/** The 10 s note was closed with ✕: no card for the rest of the visit. */
export function markNoteClosed(): void { ssSet(NOTE_CLOSED_KEY, '1') }

/** The visitor left a contact in the chat this visit (ChatWindow): no second chance for the row (rowAgainOpen). */
export function markContactLeft(): void { ssSet(CONTACT_LEFT_KEY, '1') }

/** Whether the row's second chance is open now (triggers.ts rowAgainOpen, read from this tab's storage). */
function rowAgainNow(): boolean {
  return rowAgainOpen(ssGet(DONE_KEY), ssGet(ROW_AGAIN_KEY), ssGet(CONTACT_LEFT_KEY) === '1')
}

/** Where the visitor came from, for the conversation the card opens (what the form sends today), with the
 *  browser's language and time zone: a phone typed the local way is placed in its country by them
 *  (app/services/phone_country.py; #3296). When the tab kept nothing of where the visit came from, the visit's
 *  PostHog session says it (utm.ts sourceToAdd), so every chat carries its source to the lead and the CRM. */
export function proactiveMetadata(): Record<string, string> {
  const lang = browserLanguage()
  const tz = timeZone()
  const utm = getUtm() as Record<string, string>
  let host = ''
  try { host = location.hostname } catch { /* no address: any referrer counts as another site's */ }
  return {
    ...utm, ...sourceToAdd(utm, posthogSessionEntry(), host),
    site: brand.id, ...(lang ? { browser_lang: lang } : {}), ...(tz ? { tz } : {}),
  }
}

/** The pill kept for this visit's pages (C2), or null. */
function readPill(): ProactiveOpening | null {
  if (lsGet(PILL_CLOSED_KEY) === '1') return null
  const raw = ssGet(PILL_KEY)
  if (!raw) return null
  try {
    return readKeptOpening(JSON.parse(raw))
  } catch {
    return null
  }
}

/** What the pill keeps of the card: its words and its channel, to open the chat with. */
function keepPill(o: ProactiveOpening): ProactiveOpening {
  return { ...(keepOpening(o) as ProactiveOpening), surface: o.surface === 'row' ? 'panel' : o.surface }
}

function kayakVisit(): boolean {
  return (getUtm().utm_source || '').toLowerCase() === 'kayak'
}

/** The visit came from a Google or Bing ad (triggers.ts adVisit, on the tab's first touch). */
function adVisitNow(): boolean {
  return adVisit(getUtm())
}

/** The visit came from a paid Meta ad (triggers.ts socialVisit, on the tab's first touch). */
function socialVisitNow(): boolean {
  return socialVisit(getUtm())
}

/** The tab's record names no source: no campaign tag, no click id, no other site before it (utm.ts namesWhereFrom).
 *  A visitor who did not accept marketing cookies is kept no source either (captureUtm `attribution`), and reads
 *  as one too: the reading moment, never a moment sooner. */
function directVisitNow(): boolean {
  try {
    return !namesWhereFrom(getUtm(), location.hostname)
  } catch {
    return false
  }
}

/** When the page first showed something, in ms on the page's clock (first-contentful-paint; for a page the
 *  browser prepared ahead and showed later, not before it was shown), or null when the browser does not say. */
function firstPaintMs(): number | null {
  try {
    const paint = performance.getEntriesByName('first-contentful-paint')[0]
    if (!paint) return null
    const nav = performance.getEntriesByType('navigation')[0] as { activationStart?: number } | undefined
    return Math.max(paint.startTime, nav?.activationStart ?? 0)
  } catch {
    return null
  }
}

/** The browser saw the page hidden at some point since `fromMs` (Chrome keeps the page's visibility changes).
 *  A browser that keeps none — Safari, the most common browser of the Kayak phones — is taken at its word that
 *  the page is in view now: counting nothing there would leave the iPhones' cards at ~9 s. What that misses is a
 *  visitor who left the tab and came back in the seconds before this script started; their card comes at most
 *  those seconds early, after the 2 s the widget watches first (triggers.ts, WATCHED_FIRST_S). */
function hiddenSince(fromMs: number): boolean {
  try {
    return performance.getEntriesByType('visibility-state').some((e) => e.name === 'hidden' && e.startTime >= fromMs)
  } catch {
    return false
  }
}

function pageTitle(): string {
  try { return document.querySelector('h1')?.textContent || '' } catch { return '' }
}

/** The trips the page gives, the newest word first: the search the visitor ran on the site, the address,
 *  what the page says it is about (bbc-signals), its title. */
function pageTrips(): (TripPayload | null)[] {
  const page = sitePage()
  const pageSource = page?.pageType === 'search' || page?.pageType === 'flight' ? 'search' : 'route_page'
  let search = ''
  try { search = location.search } catch { /* no address: the rest */ }
  return [
    tripFromFields(lastSearch(), 'search'),
    tripFromUrl(search, kayakVisit()),
    tripFromFields(page?.trip, pageSource),
    tripFromTitle(pageTitle()),
  ]
}

/** The trip the card and the note start from (tripContext.pickTrip): the newest of the page's trips. */
export function currentTrip(): TripPayload | null {
  return pickTrip(...pageTrips())
}

/** The ads card's trip: the newest of the page's trips that names a destination. An address with only an
 *  origin (`?gclid=…&from=JFK`) must not hide the destination the page's title names (CodeRabbit #269); the
 *  moment and the words both take this one. */
function destinationTrip(): TripPayload | null {
  return pickTrip(...pageTrips().filter(hasContext))
}

/** What the ad visitor searched before the click (utm_term, triggers.ts adKeyword), on an ad visit only. */
function adKeywordNow(): string | null {
  return adVisitNow() ? adKeyword(getUtm().utm_term) : null
}

/** The trip the ads card asks with (ADR A5): the page's destination, and the ad's search beside it — the server
 *  reads the cabin from it, and the place when the page names none (the home page). */
function adsTrip(): TripPayload | null {
  const dest = destinationTrip()
  const keyword = adKeywordNow()
  if (!keyword) return dest
  return { ...(dest ?? { source: 'route_page' as const }), keyword }
}

/** The trip a moment asks the server with: the ads card the trip that made it due (adsTrip), the others the page's. */
function tripFor(trigger: Trigger): TripPayload | null {
  return trigger === 'ads' ? adsTrip() : currentTrip()
}

/** The cards whose words name the trip they were asked with (the ads card, the Meta ad's card): an answer that comes
 *  after the page named another is stale (momentPassed, 'trip_changed'). */
const NAMES_ITS_TRIP: readonly Trigger[] = ['ads', 'social']

/** When the site asked for this script (seconds after the page opened) and how long it took to arrive, from the
 *  browser's resource timing (ADR A2): whether the widget starts late because the site asks for it late or because
 *  it is slow to come. Nothing when the browser does not say. */
function scriptTiming(): { script_req_s?: number; script_dl_ms?: number } {
  try {
    const e = performance.getEntriesByType('resource')
      .find((r) => /-widget\.js(?:[?#]|$)/.test(r.name)) as PerformanceResourceTiming | undefined
    if (!e) return {}
    return {
      script_req_s: Math.round(e.startTime / 100) / 10,
      script_dl_ms: Math.round(Math.max(0, e.responseEnd - e.startTime)),
    }
  } catch {
    return {}
  }
}

// A form the site asks for a quote with (form.qf, or one labelled as a quote): the quote window's watch reads the same.
export { quoteForm }

/** Calls `onWork(at)` each time the visitor works on the site's quote form on this page, pressed or not:
 *  a keystroke, focus, change or click inside it (not in the widget), or the site saying so (bbc-signals,
 *  also what it queued before the widget loaded). Returns the way to stop. For the 10 s note (Widget.tsx);
 *  the moments read the same touches in useProactive below. */
export function watchQuoteForm(onWork: (at: number) => void): () => void {
  const onPage = (e: Event) => {
    const t = e.target
    if (t instanceof Element && !t.closest('#bbc-widget-root') && quoteForm(t)) onWork(Date.now())
  }
  const onSite = (e: SiteEvent) => {
    if (isQuoteFormWork(e)) onWork(Math.min(e.at, Date.now()))
  }
  const types = ['keydown', 'input', 'change', 'focusin', 'click'] as const
  const cap: AddEventListenerOptions = { capture: true, passive: true }
  for (const type of types) document.addEventListener(type, onPage, cap)
  for (const e of siteEvents()) onSite(e)
  const offSite = onSiteEvent(onSite)
  return () => {
    for (const type of types) document.removeEventListener(type, onPage, cap)
    offSite()
  }
}

/** The kind of a field the browser refused (siteSignals.kindOfField): from its type, then a contact token of
 *  its autocomplete, then the first of its name and id that names a kind — never what was typed in it. */
function refusedKind(t: EventTarget | null): FieldKind | null {
  if (t instanceof HTMLInputElement && t.type === 'email') return 'email'
  if (t instanceof HTMLInputElement && t.type === 'tel') return 'phone'
  if (!(t instanceof HTMLInputElement || t instanceof HTMLSelectElement || t instanceof HTMLTextAreaElement)) return null
  return kindOfField(t.getAttribute('autocomplete'), t.name, t.id)
}

function isSubmitControl(el: Element): boolean {
  const control = el.closest('button, input[type="submit"]')
  if (!control) return false
  if (control instanceof HTMLInputElement) return control.type === 'submit'
  return (control.getAttribute('type') || 'submit').toLowerCase() === 'submit'
}

/** The form's submit button, by the rule a press is read with (a <button> without a type submits too). */
function submitControl(form: HTMLFormElement): HTMLButtonElement | HTMLInputElement | null {
  const controls = form.querySelectorAll<HTMLButtonElement | HTMLInputElement>('button, input[type="submit"]')
  return Array.from(controls).find(isSubmitControl) ?? null
}

/** The quote form says a send is in flight, for as long as it says so: time passing is no sign the send
 *  is done. `aria-busy` on the form or its button says it outright. A disabled button says it when every
 *  field is right: a disabled button takes no press, so after one it is the send that disabled it, unless
 *  a field is flagged — then the form is waiting for the visitor, not for the server. */
function sending(form: HTMLFormElement | null): boolean {
  if (!form) return false
  const btn = submitControl(form)
  if (form.getAttribute('aria-busy') === 'true' || btn?.getAttribute('aria-busy') === 'true') return true
  return !!btn && btn.disabled && !form.querySelector(':invalid, [aria-invalid="true"]')
}

interface Press {
  at: number
  form: HTMLFormElement | null
  activityAt: number
  invalid: boolean
}

export interface Proactive {
  /** What to show now (a card on a phone, the panel on a computer), or null. */
  offer: ProactiveOpening | null
  /** A card was shown in this visit: the 10 s note stays away. */
  quiet: boolean
  /** The chat opened from the offer: the card goes away (the measurements keep it). */
  opened: () => void
  /** An agent's hello came for this visitor ("On the site now"): Alex's card, unanswered, makes way for it and
   *  doesn't come back after it (no dismissal: the visitor said nothing to Alex). */
  gaveWay: () => void
  /** "Not now" or ✕ before the conversation existed: 24 h of quiet. */
  dismiss: (how: 'not_now' | 'close') => void
  /** The visitor answered (a button or typed text): measured once. */
  engaged: (reply: string) => void
  /** The visitor tapped the 10 s note or the corner button (`door`): Alex's words for this page, from the
   *  server like a card's (the note's door, trigger "note"), to open the chat with and no form. Null when
   *  the server did not answer in time: the caller falls back to the form, as before. Null too, with
   *  nothing recorded, when `stillWanted` says the visitor did something else while the words were on
   *  their way. */
  noteOpening: (stillWanted?: () => boolean, door?: 'note' | 'button') => Promise<ProactiveOpening | null>
  /** D1: the form the row stands beside, while the row's offer is on screen. */
  rowForm: HTMLFormElement | null
  /** C2: the pill after "Not now" — the card's channel, once per visit, until ✕ or a tap. */
  pill: ProactiveOpening | null
  /** ✕ on the pill: never again in this browser. */
  closePill: () => void
  /** The pill was tapped (its chat opens): it goes. */
  pillUsed: () => void
  /** E1 after the card (overlay.ts cardAsideWatch): a popup of the site covers the page, so the card waits in
   *  the corner button (a tap on it still opens Alex's chat) and comes back when the popup goes. */
  aside: boolean
}

/** proactive_quiet for the moments of this page (quietReasons): once a page, however often the hook runs again. */
let pageQuietSent = false

export function useProactive({ apiUrl, step, allowed, rowAgain = false, restored = null, embedded = false }: {
  apiUrl: string
  step: string
  allowed: boolean
  /** The CRM's iframe: an agent's screen, no visit, so nothing is measured (the moments never come there either). */
  embedded?: boolean
  /** The row's second chance may come on this page (triggers.ts rowAgainOpen), also for a visitor with a conversation
   *  (`allowed` is false then: the card's chat they left without a contact is that conversation). Never in the CRM's
   *  iframe. */
  rowAgain?: boolean
  /** The opening the panel came back with on this page (kept with it): its answer or dismissal is measured. */
  restored?: ProactiveOpening | null
}): Proactive {
  const [offer, setOffer] = useState<ProactiveOpening | null>(null)
  const [quiet, setQuiet] = useState(() => ssGet(DONE_KEY) === 'shown')
  const [rowForm, setRowForm] = useState<HTMLFormElement | null>(null)
  const [pill, setPill] = useState<ProactiveOpening | null>(() => readPill())
  const [aside, setAside] = useState(false)
  // E1 (overlay.ts): one watch of the site's popups for the page, so a popup's minute is counted once — by the
  // moments before the card and by the card once it is on screen (two clocks made the card wait a minute more).
  const [pagePopups] = useState(watchPopups)
  // The quote card's own form (the one pressed): its modal is the moment, never a popup to step aside for.
  const cardFormRef = useRef<Element | null>(null)
  const shownRef = useRef<ProactiveOpening | null>(restored)
  const engagedRef = useRef(false)
  const stepRef = useRef(step)
  stepRef.current = step
  const allowedRef = useRef(allowed)
  allowedRef.current = allowed

  // proactive_quiet with `scope: form_left` (test 1, step 1): after each quote window left (quoteWindow.ts), every
  // reason the form_left moment would not come (triggers.ts formLeftBlocks). Nothing when none holds: those are the
  // visitors the card can reach (quote_window_closed less these). Typing counts outside the quote form only: a visitor
  // who typed in the window and closed it is one who left it.
  useEffect(() => {
    if (embedded) return
    let typedAt: number | null = null
    const onType = (e: Event) => {
      const t = e.target
      if (!(t instanceof HTMLElement) || t.closest('#bbc-widget-root') || quoteForm(t)) return
      if (t.matches('input, textarea, select') || t.isContentEditable) typedAt = Date.now()
    }
    const cap: AddEventListenerOptions = { capture: true, passive: true }
    document.addEventListener('keydown', onType, cap)
    document.addEventListener('input', onType, cap)
    const off = onQuoteWindowClosed((c) => {
      const now = Date.now()
      const reasons = formLeftBlocks({
        how: c.how,
        pageVisible: document.visibilityState === 'visible',
        sitePopup: pagePopups.open(),
        sinceSiteTypingS: typedAt === null ? null : (now - typedAt) / 1000,
        chatOpen: stepRef.current !== 'buttons',
        hasChat: !allowedRef.current,
        alreadyUsed: !formLeftOpenNow(),
        visitorId: !!getVisitorId(),
      })
      if (reasons.length) {
        posthog?.capture('proactive_quiet', { scope: 'form_left', reasons, returning: isReturning(), site: brand.id })
      }
    })
    return () => {
      off()
      document.removeEventListener('keydown', onType, cap)
      document.removeEventListener('input', onType, cap)
    }
  }, [embedded])

  // The visitor opened the chat themselves (the corner button) before any moment: not this visit. A moment only
  // asked for ('asked': its answer still on its way) gives way to it, as to the site's stop below, so an answer
  // that gives the visit its moment back (trigger_off) cannot undo the chat they opened (CodeRabbit #269).
  useEffect(() => {
    if (step === 'buttons') return
    const done = ssGet(DONE_KEY)
    if (!done || done === 'asked') ssSet(DONE_KEY, 'chat')
  }, [step])

  // The site says the visitor sent the quote request, or reached out by phone, WhatsApp or email
  // (bbc-signals: quote_form_submitted, contact_clicked): no moment and no 10 s note for the rest of the
  // visit — they are already talking to us. Read from what the page queued before the widget loaded too.
  useEffect(() => {
    const stopFor = (e: SiteEvent) => {
      const { stop } = applySiteEvent(null, e)
      if (!stop) return
      // The row's second chance closes (rowAgainOpen): they sent the request, or reached out.
      const again = ssGet(ROW_AGAIN_KEY)
      if (!again || again === 'asked') ssSet(ROW_AGAIN_KEY, stop)
      // A moment this visit already used keeps its word ('shown' keeps the next pages quiet too). One only
      // asked for ('asked': its answer still on its way) gives way to the site's word, so an answer that then
      // gives the visit its moment back (trigger_off) cannot undo a sent form (CodeRabbit #269).
      const done = ssGet(DONE_KEY)
      if (!done || done === 'asked') ssSet(DONE_KEY, stop)
      ssSet(ATTENTION_KEY, '1')
      // A note already waiting (or on screen) goes away too: Widget shows it only while not quiet.
      setQuiet(true)
      // So does a card still waiting for an answer (an answered one became the chat: `opened` cleared it).
      setOffer(null)
    }
    for (const e of siteEvents()) stopFor(e)
    return onSiteEvent(stopFor)
  }, [])

  // E1 after the card: while the card is on screen, a popup of the site that opens over the page puts it
  // aside until the popup goes (overlay.ts cardAsideWatch). Only the phone card: the computer's panel has room.
  useEffect(() => {
    setAside(false)
    if (!offer || offer.surface !== 'card' || step !== 'buttons') return
    const o = offer
    // The quote card looks past the quote form's own modal, as its moment did (CodeRabbit #276): a phone visitor
    // on the site's quote modal still sees the card; any other popup still puts it aside.
    const form = o.trigger === 'quote' ? cardFormRef.current : null
    const look = cardAsideWatch(pagePopups, (on) => {
      setAside(on)
      posthog?.capture(on ? 'proactive_card_aside' : 'proactive_card_back', { trigger: o.trigger, site: brand.id })
    }, form ? (el: Element) => el.contains(form) : undefined)
    look()
    // A quarter of a second: the card makes room while the site's popup is still fading in.
    const t = setInterval(look, 250)
    return () => clearInterval(t)
  }, [offer, step])

  useEffect(() => {
    if (step !== 'buttons') return
    // A visit has one moment. In one whose card brought no contact the row beside a refused form may still come, once
    // (triggers.ts rowAgainOpen): this run then watches for that moment alone, also over a conversation the visitor
    // has (the card's chat they left without a contact).
    const rowChance = rowAgain && rowAgainNow()
    // Every reason the moments don't start on this page, gathered before the first one returns (proactive_quiet,
    // test 1 step 1): the visitors no card could reach, by cause. Once a page.
    const quiet = quietReasons({
      hasChat: !allowed, tabUsed: !!ssGet(DONE_KEY), pause: pauseRunning(lsGet(PAUSE_KEY), Date.now()),
      noteClosed: !!ssGet(NOTE_CLOSED_KEY), rowOnly: rowChance,
    })
    if (quiet.length) {
      if (!embedded && !pageQuietSent) {
        pageQuietSent = true
        posthog?.capture('proactive_quiet', { reasons: quiet, returning: isReturning(), site: brand.id })
      }
      // Test 1's form-only round (owner, 27 Nov): the visit's moment used, the 24 h pause or the closed note leave the
      // form_left moment and the row beside a refused form, once a visit and once in 24 h (formLeftTest.ts), as the
      // row's second chance comes after a card. A conversation stops it, and the CRM's iframe has none.
      if (embedded || !allowed || !formLeftOpenNow()) return
    }
    const formOnly = quiet.length > 0
    const rowOnly = rowChance && !formOnly

    let disposed = false
    let fired = false
    // Test 1's moment on this page: asked apart from the others, so a card shown earlier on the page (88% of those who
    // leave the window had one) does not keep it away; while it is on its way no other moment is asked.
    let formFired = false
    let warnedShape = false
    let sentInline = false
    let popupHeldOnce = false
    // How many times this page asked again after the trip changed under an answer (triggers.ts givesMomentBack).
    let tripAskedAgain = 0
    // What this run's moment reserves while its answer is on the way: the visit's one moment, or the row's second
    // chance (rowOnly). An answer that gives it back frees only its own 'asked': a stop the site said meanwhile stays.
    const baseHeld = rowOnly ? ROW_AGAIN_KEY : DONE_KEY
    // Test 1's moments hold their own key (FORM_KEY): set when one is asked (evaluate).
    let held = baseHeld
    // This run's own reservation while nothing is decided about its answer (on its way, or waiting for a popup): the
    // run that ends first gives it back (the cleanup below), and an answer that comes after its run ended gives back
    // nothing, so it never frees a newer run's 'asked' (CodeRabbit #282, round 8).
    let pending = false
    const giveBack = () => {
      pending = false
      if (!disposed && ssGet(held) === 'asked') ssSet(held, '')
    }
    const start = Date.now()
    // E1 (overlay.ts): the site's popups over the page; a card waits while one is open.
    const popups = pagePopups
    // The moments count the visitor's seconds, not the widget's (triggers.ts, "in view"): the time the page
    // was in view before this script started counts too. `widgetStartS`: how long after the page showed this
    // script started, for the measurements (null when the browser does not say when the page showed).
    const pageNowMs = typeof performance !== 'undefined' ? performance.now() : NaN
    const paintMs = firstPaintMs()
    const inView = document.visibilityState === 'visible'
    let visibleAcc = inViewBeforeStartMs(pageNowMs, paintMs, inView, paintMs !== null && hiddenSince(paintMs))
    const widgetStartS = paintMs !== null && Number.isFinite(pageNowMs)
      ? Math.round(Math.max(0, pageNowMs - paintMs) / 100) / 10
      : null
    let visibleSince: number | null = inView ? start : null
    let lastActivity = start
    // Scrolled before the script started: that counts for the reader too.
    let maxScroll = window.scrollY || document.documentElement.scrollTop || 0
    let lastSiteTyping: number | null = null
    // The same, outside the quote form: typing in the window and closing it is leaving it (triggers.ts formLeftBlocks).
    let lastOtherTyping: number | null = null
    // The quote window the visitor last left on this page (quoteWindow.ts), for test 1's moment.
    let left: QuoteWindowClosed | null = null
    let press: Press | null = null
    // The last time the visitor worked on the quote form, pressed or not (triggers.ts: no card over it).
    let quoteFormAt: number | null = null
    // The kinds of the fields the last refused send flagged (the site's word, or the browser's): the quote
    // card speaks to a form refused for its contact fields (the server's words; owner, 18 Nov). One send
    // only: a press starts it, and the site's word joins the browser's for that press (siteSaidFor).
    let refused: FieldKind[] = []
    // The press the site's last refusal belonged to: a second word of the site with no press of ours
    // between is a send of its own, and starts a list of its own (CodeRabbit #268, round 3).
    let siteSaidFor: number | null = null
    let exitAt = 0
    // What the site itself says about its quote form (siteSignals.applySiteEvent): a send in flight, and
    // whether the visitor already sent the request or reached out (then nothing on this page).
    let siteBusy = false
    let stopped: 'sent' | 'contact' | null = null

    const visibleS = () => (visibleAcc + (visibleSince !== null ? Date.now() - visibleSince : 0)) / 1000
    const inWidget = (t: EventTarget | null) => t instanceof Element && !!t.closest('#bbc-widget-root')
    const inPressedForm = (t: EventTarget | null) => !!press?.form && t instanceof Node && press.form.contains(t)
    const isField = (t: EventTarget | null) =>
      t instanceof HTMLElement && (t.matches('input, textarea, select') || t.isContentEditable)
    // Work on the pressed form: inside it, or any field of the site for a press whose form is unknown (triggers.ts).
    const worksOnPressed = (t: EventTarget | null) => !!press && worksOnPressedForm({
      formKnown: !!press.form, inForm: inPressedForm(t), inWidget: inWidget(t), field: isField(t),
    })
    // A touch inside the site's quote form (quoteForm: form.qf, or one labelled as a quote), pressed or not.
    const touchQuoteForm = (t: EventTarget | null) => {
      if (t instanceof Element && !inWidget(t) && quoteForm(t)) quoteFormAt = Date.now()
    }

    const onActivity = () => { lastActivity = Date.now() }
    const onScroll = () => {
      lastActivity = Date.now()
      maxScroll = Math.max(maxScroll, window.scrollY || document.documentElement.scrollTop || 0)
    }
    const onVisibility = () => {
      if (document.visibilityState === 'visible') {
        if (visibleSince === null) visibleSince = Date.now()
      } else if (visibleSince !== null) {
        visibleAcc += Date.now() - visibleSince
        visibleSince = null
      }
    }
    // Typing: in a field of the site it holds every moment off for a few seconds; in the quote form it
    // also says the form is being worked on.
    const onType = (e: Event) => {
      lastActivity = Date.now()
      if (inWidget(e.target)) return
      if (isField(e.target)) lastSiteTyping = Date.now()
      if (isField(e.target) && !quoteForm(e.target as Element)) lastOtherTyping = Date.now()
      touchQuoteForm(e.target)
      if (press && worksOnPressed(e.target)) press.activityAt = Date.now()
    }
    const onFormTouch = (e: Event) => {
      touchQuoteForm(e.target)
      if (press && worksOnPressed(e.target)) press.activityAt = Date.now()
    }
    const onClick = (e: Event) => {
      lastActivity = Date.now()
      const el = e.target instanceof Element ? e.target : null
      if (!el || inWidget(el)) return
      touchQuoteForm(el)
      const form = quoteForm(el)
      const control = el.closest('button, a, [role="button"], input[type="submit"]')
      const text = control ? (control.textContent || (control as HTMLInputElement).value || '') : ''
      const kind = quoteClick({
        knownSubmit: !!form && isSubmitControl(el), works: worksOnPressed(el),
        quoteControl: !!control && QUOTE_TEXT.test(text), inKnownForm: !!form,
      })
      if (kind === 'press' && form) {
        press = { at: Date.now(), form, activityAt: Date.now(), invalid: false }
        refused = []
        return
      }
      if (kind === 'work' && press) {
        press.activityAt = Date.now()
        return
      }
      if (kind === 'press_unknown') {
        // The site's button without the form we know: still a press, and the HTML changed — say so.
        press = { at: Date.now(), form: null, activityAt: Date.now(), invalid: false }
        refused = []
        if (!warnedShape) {
          warnedShape = true
          posthog?.capture('proactive_quote_unrecognized', { path: location.pathname, site: brand.id })
        }
      }
    }
    // Enter in a field sends the form too.
    const onSubmit = (e: Event) => {
      const form = e.target instanceof HTMLFormElement ? quoteForm(e.target) : null
      if (form) {
        press = { at: Date.now(), form, activityAt: Date.now(), invalid: press?.invalid ?? false }
        refused = []  // a send of its own: the kinds of an earlier refusal are not this one's
      }
    }
    // A refused send is answered at once (triggers.ts QUOTE_ROW_IDLE_S), not at the next second of the clock below:
    // after the last `invalid` of that send, so the row knows every field the form flagged.
    let refusalTimer: ReturnType<typeof setTimeout> | null = null
    const refusedNow = () => {
      if (refusalTimer !== null) return
      refusalTimer = setTimeout(() => {
        refusalTimer = null
        evaluate()
      }, 0)
    }
    // The browser refused the send and flagged a field (`invalid` does not bubble: caught on the way down).
    const onInvalid = (e: Event) => {
      if (!press || !inPressedForm(e.target)) return
      press.invalid = true
      press.activityAt = Date.now()
      // One `invalid` per refused field, all in the same send: together they are that send's refusal.
      refused = withKinds(refused, [refusedKind(e.target)])
      refusedNow()
    }
    const onLeave = (e: MouseEvent) => {
      if (e.relatedTarget || e.clientY > 8) return
      exitAt = Date.now()
      evaluate()
    }
    // The site's word about the visitor (bbc-signals): a refused or failed send of the quote form is a
    // press with a flagged field, a send in flight holds every moment off, and working on the form keeps
    // it from going quiet. It joins what the page's HTML shows; a site without bbc-signals keeps that alone.
    const onSite = (e: SiteEvent) => {
      lastActivity = Math.max(lastActivity, Math.min(e.at, Date.now()))
      // The site says the visitor is in its quote form (opened it, moved a step, a field refused): no card
      // over it, pressed or not. A refused or failed send is a press too (applySiteEvent, below).
      if (isQuoteFormWork(e)) quoteFormAt = Math.max(quoteFormAt ?? 0, Math.min(e.at, Date.now()))
      // The site names the fields of a refused send itself. Its first word for our press, right after it,
      // joins the browser's kinds of that send (a word without fields takes nothing away); any other word is
      // a send of its own.
      const siteRefused = e.name === 'quote_form_invalid'
      if (siteRefused) {
        const kinds = fieldKinds(e.detail.fields)
        const sameSend = !!press && siteSaidFor !== press.at && e.at - press.at <= SAME_SEND_MS
        refused = sameSend ? withKinds(refused, kinds) : kinds
      }
      const fx = applySiteEvent(
        press ? { at: press.at, activityAt: press.activityAt, invalid: press.invalid, busy: siteBusy } : null, e,
      )
      if (fx.stop) {
        stopped = fx.stop
        press = null
        return
      }
      siteBusy = fx.press?.busy ?? false
      press = fx.press
        ? { form: press?.form ?? null, at: fx.press.at, activityAt: fx.press.activityAt, invalid: fx.press.invalid }
        : null
      if (siteRefused) siteSaidFor = press?.at ?? null
      // The site's word about a refused or failed send is heard at once too (the browser's, onInvalid above).
      if (press?.invalid && (siteRefused || e.name === 'quote_form_failed')) refusedNow()
    }

    // What the moment was decided on. The server answers within OFFER_TIMEOUT_MS; by then the visitor
    // may have moved on, and an answer that comes after that is dropped (and measured), never shown.
    // `search`: the site's last search (each one a new object, siteSignals.cleanDetail): a new search on the
    // way makes the trip the answer was asked with an old one.
    const seen = () => ({
      typing: lastSiteTyping, pressAt: press?.at ?? null, formAt: press?.activityAt ?? null,
      quoteForm: quoteFormAt, search: lastSearch(),
      // The quote moment asks for the row beside the pressed form (D1) when the widget still sees that form.
      row: !!press?.form?.isConnected,
    })
    const momentPassed = (trigger: Trigger, before: ReturnType<typeof seen>, askedTrip: string | null): string | null => {
      if (stopped) return stopped
      // The row's second chance ends with a contact left in the chat, also one a send finishing after the chat closed
      // marked while its answer was on the way (CodeRabbit #282, round 7): the row is dropped, measured. Its 'asked'
      // needs no giving back: the contact keeps the chance closed (rowAgainOpen).
      if (rowOnly && ssGet(CONTACT_LEFT_KEY) === '1') return 'contact_left'
      if (stepRef.current !== 'buttons') return 'chat_opened'
      if (document.visibilityState !== 'visible') return 'hidden'
      // The note closed with ✕ holds every card but test 1's (owner, 27 Nov: as "Not now", it does not stop it).
      if (trigger !== 'form_left' && !formOnly && ssGet(NOTE_CLOSED_KEY)) return 'note_closed'
      if (blockedPath(location.pathname)) return 'left_page'
      // The site moved on to its thank-you or a legal page while the answer was on its way.
      if (noMomentPage(sitePage())) return 'left_page'
      // The row covers nothing and comes at once (QUOTE_ROW_IDLE_S): a visitor fixing the refused form while its
      // answer was on the way still gets it. A new press is a new send, and drops it.
      const forRow = trigger === 'quote' && before.row
      if (!forRow && lastSiteTyping !== before.typing) return 'typing'
      if ((press?.at ?? null) !== before.pressAt || (!forRow && (press?.activityAt ?? null) !== before.formAt)) return 'quote_form'
      // Work on the quote form while the answer was on its way, pressed or not, holds kayak and reader
      // (triggers.ts); the quote moment keeps the press check above (CodeRabbit #267).
      if (trigger !== 'quote' && quoteFormAt !== before.quoteForm) return 'quote_form'
      if (lastSearch() !== before.search) return 'searched'
      // The ads card and the Meta ad's card name the destination they were asked with: another one on the page by
      // now (its address, its title or the site's word changed) makes those words stale (CodeRabbit #269, #282). Each
      // against the trip its own request was built with (tripFor).
      if (askedTrip !== null && JSON.stringify(tripFor(trigger)) !== askedTrip) return 'trip_changed'
      if (trigger === 'quote' && press?.form && (!press.form.isConnected || sending(press.form))) return 'sent'
      return null
    }

    // `reading`: the moment came at the reading moment (triggers.ts), measured as `moment: 'reading'`.
    // `leftAsked`: the window left that brought test 1's moment (its how and what was done in it, never a value).
    const ask = async (trigger: Trigger, phone: boolean, reading = false, leftAsked: QuoteWindowClosed | null = null) => {
      // Test 1's row: the row beside a refused form asked in the form-only round, answered by the test's arms.
      const afterCard = formOnly && trigger === 'quote'
      // An answer that gives its moment back frees the ask it came for: test 1's own, or the page's.
      const release = () => {
        if (trigger === 'form_left') formFired = false
        else fired = false
      }
      const before = seen()
      // The ads card asks with the trip that made it due (adsTrip): the words name that destination.
      const trip = tripFor(trigger)
      const askedTrip = NAMES_ITS_TRIP.includes(trigger) ? JSON.stringify(trip) : null
      const device = phone ? 'phone' : 'computer'
      // `refused`: the kinds of the fields the quote form refused (never what was typed), for the quote card.
      const took: OfferTiming[] = []
      // D1: the row stands beside the form that was pressed, when the widget can still see it.
      const rowAt = trigger === 'quote' && press?.form?.isConnected ? press.form : null
      // The refusal this answer is asked for: the site may name the same send's fields again while the answer is
      // on its way (onSite), and the row asks for what the form had when the server was asked (CodeRabbit #279).
      const refusedAsked = trigger === 'quote' ? [...refused] : []
      const lang = browserLanguage()
      // The visitor this answer is asked for: test 1's answer is theirs alone (formLeftTest.ts stillVisitor).
      const askedFor = ensureVisitorId()
      const res = await fetchProactiveOffer(apiUrl, {
        visitor_id: askedFor, trigger, trip, site: brand.id,
        ...(refusedAsked.length ? { refused: refusedAsked } : {}),
        ...(lang ? { lang } : {}),
        device,
        ...(rowAt ? { place: 'form' as const } : {}),
        // This widget opens the contact field with the yes's answer (the owner's contact test, ChatWindow).
        opens_field: true,
        ...(leftAsked ? {
          left: { how: leftAsked.how, source: leftAsked.source, focused: leftAsked.focused, typed: leftAsked.typed },
        } : {}),
        ...(afterCard ? { after_card: true } : {}),
      }, OFFER_TIMEOUT_MS, (t) => took.push(t))
      // Test 1's answer is the visitor's it was asked for. For one this browser no longer has (a form for someone
      // else meanwhile, in any tab), the visitor of now gets nothing of it: no side, no event, no card. Their
      // reservation goes back. Read when the answer comes and again after a popup's wait (CodeRabbit #291).
      const goneVisitor = (): boolean => {
        if (!(trigger === 'form_left' || afterCard) || stillVisitor(askedFor)) return false
        giveBack()
        release()
        return true
      }
      if (goneVisitor()) return
      // The owner's agent-first test: the side this answer names is the visit's, on every event from here on.
      if (res) noteSide(res.test)
      let passed = !disposed && res?.show && res.message ? momentPassed(trigger, before, askedTrip) : null
      // E1: a popup of the site opened while the answer was on its way — the card waits for it to close. The
      // quote card too, looking past the quote form's own modal as its moment did (CodeRabbit #270).
      const pressed = press?.form ?? null
      const ignore = trigger === 'quote' ? (el: Element) => !!pressed && el.contains(pressed) : undefined
      if (!passed && !disposed && res?.show && res.message && popups.open(ignore)) {
        await untilPopupCloses(ignore)
        passed = disposed ? 'popup' : momentPassed(trigger, before, askedTrip) ?? (popups.open(ignore) ? 'popup' : null)
        if (goneVisitor()) return
      }
      // `site` on every proactive_* event (ADR 18): BBC and BCT are compared, never mixed.
      posthog?.capture('proactive_eligible', {
        trigger, arm: res ? res.arm : 'no_answer', device, has_context: hasContext(trip), site: brand.id,
        ...(reading ? { moment: 'reading' } : {}),
        // The page carries bbc-signals: the moments with the site's word against those without it.
        site_signals: !!sitePage(),
        // How long the answer took and why none came (offer_ms, offer_fail), and how long after the page
        // showed this script started: what OFFER_TIMEOUT_MS and the "in view" count rest on (triggers.ts).
        ...offerMeasures(took[0]),
        ...(widgetStartS !== null ? { widget_start_s: widgetStartS } : {}),
        ...scriptTiming(),
        ...(passed ? { moment_passed: passed } : {}),
        ...(res?.lang ? { lang: res.lang } : {}),
        // The ad's search asked with (never its words): the server reads a place from it or switches ads off.
        ...(trigger === 'ads' && trip?.keyword ? { ad_keyword: true } : {}),
        // The row's second chance (rowAgainOpen): its moments counted apart from the visit's first.
        ...(rowOnly ? { again: true } : {}),
        // Test 1: how the window was left, and the row asked after a card (its arms, apart from today's row).
        ...(leftAsked ? { left_how: leftAsked.how } : {}),
        ...(afterCard ? { after_card: true } : {}),
      })
      if (res && !res.show && res.arm === FORM_LEFT_OFF_ARM) {
        // Test 1's off arm: asked and counted, never shown. The visit keeps its moment for the other cards, as today
        // (the comparison rests on it), and the test does not ask again in it.
        giveBack()
        ssSet(FORM_KEY, 'used')
        release()
        return
      }
      if (res && !res.show && res.arm === TRIGGER_OFF_ARM && trigger === 'ads' && JSON.stringify(adsTrip()) !== askedTrip) {
        // A "no" about the ad's trip as it was: the page named another meanwhile, so it says nothing about this one.
        // The reservation goes back and the moment is asked again with the trip of now (CodeRabbit #272).
        giveBack()
        release()
        return
      }
      if (res && !res.show && res.arm === TRIGGER_OFF_ARM) {
        // This moment is switched off on the server (PROACTIVE_TRIGGERS), not the feature: it is not asked again
        // in this visit, and the visit keeps its one moment for the others (the reader may still come).
        offInPage = offAfter(offInPage, trigger)
        // The whole list this visit knows, not the tab's alone: a moment whose own write was refused rides on the
        // next write that goes through (CodeRabbit #269).
        ssSet(OFF_KEY, offMoments().join(','))
        // Only the reservation this answer owned: a stop the site said meanwhile ('sent') stays.
        giveBack()
        release()
        return
      }
      if (res && !res.show && res.arm === AGENT_FIRST_ARM && res.retry_after_s) {
        // The owner's agent-first test (agentTest.ts): Alex waits for an agent, who sees the visitor in "On the
        // site now" with the time he comes. The moment goes back; evaluate asks again once the wait is over.
        // A "no" from this arm (an agent said hello, or the visitor said not now) keeps the moment used.
        holdAlex(res.retry_after_s, res.alex_at)
        giveBack()
        release()
        return
      }
      if (givesMomentBack(passed, tripAskedAgain)) {
        // The card would name the destination the page showed before (CodeRabbit #282, round 5): dropped and
        // measured above, and the moment goes back; evaluate asks again with the trip of now if it is still due.
        tripAskedAgain++
        giveBack()
        release()
        return
      }
      if (givesRowBack(passed, rowOnly)) {
        // The row's second chance, dropped for a new press of the form (measured above): that send may be refused
        // too, and its refusal is the row's moment then (CodeRabbit #282, round 8).
        giveBack()
        release()
        return
      }
      if (disposed || !res || !res.show || !res.message || passed) {
        // Dropped, or no answer: the moment (or the second chance) is used, as asked.
        pending = false
        return
      }
      // D1: the server gave the row's words, and the form is still there to stand beside.
      const row = res.variant === 'row' && !!res.buttons?.length && !!rowAt?.isConnected
      if ((res.variant === 'row' || (rowOnly && trigger !== 'form_left')) && !row) {
        // The form went away meanwhile: nothing to stand beside, and this visit's moment is given back. The row's second
        // chance is for the row alone: any other answer gives it back too.
        giveBack()
        release()
        return
      }
      const surface: Surface = row ? 'row' : phone ? 'card' : 'panel'
      const opening: ProactiveOpening = {
        trigger, message: res.message, quickReplies: res.quick_replies, trip, surface, shownAt: Date.now(),
        ...(res.variant ? { variant: res.variant } : {}),
        ...(res.confirm ? { confirm: res.confirm } : {}),
        ...(res.channel ? { channel: res.channel } : {}),
        ...(res.buttons ? { buttons: res.buttons } : {}),
        ...(res.lang ? { lang: res.lang } : {}),
        ...(res.headline && res.card_text ? { headline: res.headline, cardText: res.card_text } : {}),
        ...(res.contact_test ? { contactTest: res.contact_test } : {}),
        // The row asks for the contact the form already had when it refused the other one ("One is enough").
        ...(row ? { rowField: rowField(refusedAsked) } : {}),
      }
      if (row) setRowForm(rowAt)
      // The visit's one moment is used now (the second chance keeps it 'shown'). Any row shown uses the row's second
      // chance: that chance itself, or the visit's moment that was the row (it had its row; the chance is after a card).
      pending = false
      if (trigger === 'form_left' || afterCard) {
        // Test 1's card: once a visit and once in 24 h (formLeftTest.ts); the visit's moment, if still free, is used too.
        ssSet(FORM_KEY, 'shown')
        noteFormLeftShown(Date.now(), askedFor)
        const done = ssGet(DONE_KEY)
        if (!done || done === 'asked') ssSet(DONE_KEY, 'shown')
      } else if (!rowOnly) ssSet(DONE_KEY, 'shown')
      if (row) ssSet(ROW_AGAIN_KEY, 'used')
      ssSet(ATTENTION_KEY, '1')
      // A new opening on screen: its answer is measured too (the card's, earlier in the visit, may have been).
      engagedRef.current = false
      shownRef.current = opening
      cardFormRef.current = trigger === 'quote' ? pressed : null
      setQuiet(true)
      setOffer(opening)
      noteAlexShown(trigger)
      posthog?.capture('proactive_shown', {
        trigger, surface, device, site: brand.id, ...(res.variant ? { variant: res.variant } : {}),
        // The opening's own time: the visit registry tells its answers from a later opening's (visitsRead.ts).
        opening_at: opening.shownAt,
        ...(reading ? { moment: 'reading' } : {}),
        // The agent-first test: the card came because Alex's wait ran out with no hello.
        ...(res.after_hold ? { after_hold: true } : {}),
        ...(res.lang ? { lang: res.lang } : {}),
        // The phone card led with the trip (ADR A3): measured against the cards that did not.
        ...(surface === 'card' && opening.headline ? { titled: true } : {}),
        // The row's field: either contact, or the one the form already had ("One is enough").
        ...(opening.rowField ? { row_field: opening.rowField } : {}),
        ...contactTestProps(opening),
        // The row's second chance, after a card that brought no contact (rowAgainOpen): measured apart.
        ...(rowOnly ? { again: true } : {}),
        ...(leftAsked ? { left_how: leftAsked.how } : {}),
        ...(afterCard ? { after_card: true } : {}),
      })
    }

    // E1: wait while a popup of the site covers the page — at most a minute, then the moment is gone.
    const untilPopupCloses = (ignore?: (el: Element) => boolean) => new Promise<void>((resolve) => {
      const until = Date.now() + 60_000
      const check = () => {
        if (disposed || !popups.open(ignore) || Date.now() > until) return resolve()
        setTimeout(check, 500)
      }
      setTimeout(check, 500)
    })

    // Test 1's moment (triggers.ts pickFormLeft): the window the visitor closed in the last FORM_LEFT_WITHIN_S, the
    // test running for this visitor (an arm) and not used in this visit nor in the last 24 h, nothing in the way.
    const formLeftDue = (now: number): QuoteWindowClosed | null => {
      const l = left
      if (!l || !formLeftArm() || !formLeftOpenNow() || offMoments().includes('form_left')) return null
      // The agent-first test: while Alex waits for an agent, no moment is asked (agentTest.ts), test 1's neither.
      if (alexWaitLeftMs(now) > 0) return null
      const blocks = formLeftBlocks({
        how: l.how, pageVisible: document.visibilityState === 'visible', sitePopup: false,
        sinceSiteTypingS: lastOtherTyping === null ? null : (now - lastOtherTyping) / 1000,
        chatOpen: stepRef.current !== 'buttons', hasChat: !allowedRef.current, alreadyUsed: false,
        visitorId: !!getVisitorId(),
      })
      return pickFormLeft((now - l.at) / 1000, blocks) ? l : null
    }

    const evaluate = () => {
      if (disposed || stopped || document.visibilityState !== 'visible') return
      // The site says this is its thank-you or a legal page (bbc-signals), whatever the address.
      if (noMomentPage(sitePage())) return
      // Test 1 first, before the other cards (the site's popup still holds it, below): the window just left is the
      // visitor leaving, in every round. A form sent in place sends no window left (quoteWindow.ts), so it is read
      // before the press's checks.
      // Asked apart from the page's moment: not while any answer is on its way (one reservation at a time).
      const leftNow = !formFired && !pending ? formLeftDue(Date.now()) : null
      if (leftNow) {
        if (popups.open()) {
          if (!popupHeldOnce) {
            popupHeldOnce = true
            posthog?.capture('proactive_popup_held', {
              trigger: 'form_left', device: matches(PHONE) ? 'phone' : 'computer', site: brand.id,
            })
          }
          return
        }
        formFired = true
        pending = true
        held = FORM_KEY
        ssSet(held, 'asked')
        void ask('form_left', matches(PHONE), false, leftNow)
        return
      }
      // The page's moment is asked once, and never while test 1's is on its way or shown.
      if (fired || formFired) return
      // The note closed with ✕ while this page was open: no other card for the rest of the visit (test 1's row in its
      // form-only round still may: the closed note does not stop test 1).
      if (ssGet(NOTE_CLOSED_KEY) && !formOnly) return
      // The row's second chance, read again each time: a contact left in a chat that sent while this ran (a send
      // finishing after the chat closed), or the site's stop, closes it.
      if (rowOnly && !rowAgainNow()) return
      // The quote form went away after the press: it was sent in place. Nothing on this page after that.
      if (press?.form && !press.form.isConnected) {
        sentInline = true
        press = null
      }
      if (sentInline) return
      // E1's minute runs from when a popup of the site appears, not from when a moment first wants to come:
      // a popup left open through Alex's wait (the agent-first test) held the card one more minute after the
      // two (secret-site run, 22 Nov: the card at 185 s instead of 125 s). The look only keeps the clock.
      popups.open()
      const now = Date.now()
      const quote: QuoteState | null = press ? {
        sincePressS: (now - press.at) / 1000,
        sinceFormActivityS: (now - Math.max(press.at, press.activityAt)) / 1000,
        invalid: press.invalid,
        busy: sending(press.form) || siteBusy,
        row: !!press.form?.isConnected,
      } : null
      const ad = adVisitNow()
      const state: PageState = {
        path: location.pathname,
        kayak: kayakVisit(),
        ad,
        // The page's trip is read only for a visit from an ad: the ads card says its destination back — the
        // page's, or the one the ad's search names (ADR A5; the server reads it and may still say no).
        destination: ad && (destinationTrip() !== null || keywordNamesPlace(adKeywordNow())),
        social: socialVisitNow(),
        direct: directVisitNow(),
        visibleS: visibleS(),
        watchedS: (now - start) / 1000,
        sinceActivityS: (now - lastActivity) / 1000,
        scrolledScreens: maxScroll / Math.max(1, window.innerHeight || 1),
        phone: matches(PHONE),
        sinceSiteTypingS: lastSiteTyping === null ? null : (now - lastSiteTyping) / 1000,
        quote,
        sinceQuoteFormS: quoteFormAt === null ? null : (now - quoteFormAt) / 1000,
        exitIntent: now - exitAt < 1500,
        off: offMoments(),
        siteSignals: !!sitePage(),
      }
      // The agent-first test: while Alex waits for an agent, no moment is asked (agentTest.ts).
      if (alexWaitLeftMs(now) > 0) return
      const trigger = pickTrigger(state)
      if (!trigger) return
      // The second chance is the row beside a refused form, never any other moment (triggers.ts rowMoment); so is test
      // 1's form-only round, for a visitor the test runs for (its row, `after_card`).
      if ((rowOnly || formOnly) && !rowMoment(trigger, quote)) return
      // Read again at every tick, not only at the run's start: the off arm's "no" spends the visit's chance (FORM_KEY
      // 'used'), and an answer on its way holds it ('asked'), so the row is never asked twice (CodeRabbit #291).
      if (formOnly && (!formLeftArm() || !formLeftOpenNow())) return
      // E1: no card over a popup of the site; it comes once the popup closes (the quote moment looks past the
      // quote form's own modal).
      const pressedForm = press?.form ?? null
      if (popups.open(trigger === 'quote' ? (el) => !!pressedForm && el.contains(pressedForm) : undefined)) {
        if (!popupHeldOnce) {
          popupHeldOnce = true
          posthog?.capture('proactive_popup_held', { trigger, device: state.phone ? 'phone' : 'computer', site: brand.id })
        }
        return
      }
      fired = true
      pending = true
      held = formOnly ? FORM_KEY : baseHeld
      ssSet(held, 'asked')
      void ask(trigger, state.phone, readingMoment(state, trigger))
    }

    const cap: AddEventListenerOptions = { capture: true, passive: true }
    const pointerEvents = ['pointerdown', 'pointermove', 'wheel', 'touchstart'] as const
    for (const ev of pointerEvents) window.addEventListener(ev, onActivity, cap)
    window.addEventListener('scroll', onScroll, cap)
    document.addEventListener('keydown', onType, cap)
    document.addEventListener('input', onType, cap)
    document.addEventListener('change', onFormTouch, cap)
    document.addEventListener('focusin', onFormTouch, cap)
    document.addEventListener('click', onClick, cap)
    document.addEventListener('submit', onSubmit, cap)
    document.addEventListener('invalid', onInvalid, cap)
    document.addEventListener('visibilitychange', onVisibility)
    const exitWatch = matches('(pointer: fine)') && !matches(PHONE)
    if (exitWatch) document.documentElement.addEventListener('mouseleave', onLeave)
    // What the site said before this watch started (the queue), then what it says from now on.
    for (const e of siteEvents()) onSite(e)
    const offSite = onSiteEvent(onSite)
    // Test 1: a window left is answered at once, not at the next second of the clock.
    const offLeft = onQuoteWindowClosed((c) => {
      left = c
      evaluate()
    })
    const tick = setInterval(evaluate, 1000)

    return () => {
      disposed = true
      // An answer still undecided when its run ends is never shown (disposed): its reservation goes back now.
      if (pending && ssGet(held) === 'asked') ssSet(held, '')
      pending = false
      clearInterval(tick)
      offSite()
      offLeft()
      if (refusalTimer !== null) clearTimeout(refusalTimer)
      for (const ev of pointerEvents) window.removeEventListener(ev, onActivity, cap)
      window.removeEventListener('scroll', onScroll, cap)
      document.removeEventListener('keydown', onType, cap)
      document.removeEventListener('input', onType, cap)
      document.removeEventListener('change', onFormTouch, cap)
      document.removeEventListener('focusin', onFormTouch, cap)
      document.removeEventListener('click', onClick, cap)
      document.removeEventListener('submit', onSubmit, cap)
      document.removeEventListener('invalid', onInvalid, cap)
      document.removeEventListener('visibilitychange', onVisibility)
      if (exitWatch) document.documentElement.removeEventListener('mouseleave', onLeave)
    }
  }, [allowed, rowAgain, step, apiUrl])

  return {
    offer,
    quiet,
    aside,
    opened: () => { setOffer(null); setRowForm(null) },
    gaveWay: () => { setOffer(null); setRowForm(null) },
    dismiss: (how) => {
      lsSet(PAUSE_KEY, String(Date.now() + PAUSE_AFTER_DISMISS_MS))
      const o = shownRef.current
      // "On the site now" lists a visitor who said no to Alex at once (agentTest.ts).
      noteAlexAnswer('dismissed')
      if (o) {
        posthog?.capture('proactive_dismissed', {
          trigger: o.trigger, how, surface: o.surface, seconds_visible: Math.round((Date.now() - o.shownAt) / 1000),
          opening_at: o.shownAt,
          site: brand.id, ...(o.variant ? { variant: o.variant } : {}),
        })
        // C2: after "Not now" the card's channel stays as a small pill, once per visit, until ✕ or a tap.
        if (how === 'not_now' && o.channel && lsGet(PILL_CLOSED_KEY) !== '1') {
          const kept = keepPill(o)
          ssSet(PILL_KEY, JSON.stringify(kept))
          setPill(kept)
          posthog?.capture('proactive_pill_shown', { trigger: o.trigger, channel: o.channel.kind, site: brand.id })
        }
      }
      setOffer(null)
      setRowForm(null)
    },
    rowForm,
    pill,
    closePill: () => {
      lsSet(PILL_CLOSED_KEY, '1')
      ssSet(PILL_KEY, '')
      if (pill) posthog?.capture('proactive_pill_closed', { trigger: pill.trigger, site: brand.id })
      setPill(null)
    },
    pillUsed: () => {
      ssSet(PILL_KEY, '')
      if (pill) posthog?.capture('proactive_pill_opened', { trigger: pill.trigger, site: brand.id })
      setPill(null)
    },
    engaged: (reply) => {
      const o = shownRef.current
      // Answered: Alex's, never listed for an agent's hello (agentTest.ts).
      noteAlexAnswer('engaged')
      if (!o || engagedRef.current) return
      engagedRef.current = true
      posthog?.capture('proactive_engaged', {
        trigger: o.trigger,
        surface: o.surface,
        reply: replyKind(reply, o.quickReplies),
        seconds_to_reply: Math.round((Date.now() - o.shownAt) / 1000),
        opening_at: o.shownAt,
        site: brand.id,
        ...(o.variant ? { variant: o.variant } : {}),
        ...contactTestProps(o),
      })
    },
    noteOpening: async (stillWanted, door = 'note') => {
      const trip = currentTrip()
      const took: OfferTiming[] = []
      const lang = browserLanguage()
      const res = await fetchProactiveOffer(
        apiUrl, {
          visitor_id: ensureVisitorId(), trigger: 'note', trip, site: brand.id, ...(lang ? { lang } : {}),
          device: matches(PHONE) ? 'phone' : 'computer', opens_field: true,
        }, OFFER_TIMEOUT_MS,
        (t) => took.push(t),
      )
      // The owner's agent-first test: the note's answer names the visit's side too, before its proactive_shown
      // (CodeRabbit #273: a note tapped before any other answer was shown with no side).
      if (res) noteSide(res.test)
      // The visitor did something else while the answer was on its way (Widget's note turn): nothing of it
      // happens, so nothing says it did — no proactive_shown, no proactive_note_unanswered (Widget opens no
      // form either), and the cards of this visit stay possible (CodeRabbit #269).
      if (stillWanted && !stillWanted()) return null
      if (!res) {
        // No answer in time: the visitor who asked for the chat gets the form (Widget.openNote). Counted, with
        // how long it waited and why, so the wait can be set from the data (triggers.ts, OFFER_TIMEOUT_MS).
        posthog?.capture('proactive_note_unanswered', {
          door, device: matches(PHONE) ? 'phone' : 'computer', site: brand.id, ...offerMeasures(took[0]),
        })
        return null
      }
      if (!res.show || !res.message) return null
      const opening: ProactiveOpening = {
        trigger: 'note', message: res.message, quickReplies: res.quick_replies, trip, surface: 'panel',
        shownAt: Date.now(),
        ...(res.variant ? { variant: res.variant } : {}),
        ...(res.confirm ? { confirm: res.confirm } : {}),
        ...(res.lang ? { lang: res.lang } : {}),
        ...(res.contact_test ? { contactTest: res.contact_test } : {}),
      }
      // The visitor opened the chat: no card follows in this visit (the moments' guard reads this).
      ssSet(DONE_KEY, 'chat')
      shownRef.current = opening
      engagedRef.current = false
      // Alex's opening in the chat they opened: unanswered, it is listed for an agent like his card (agentTest.ts).
      noteAlexShown('note')
      // `variant`: the note's bandit compares its two first questions from the tap on, not only from the
      // chats that started (the server counts those).
      posthog?.capture('proactive_shown', {
        trigger: 'note', surface: 'panel', door, device: matches(PHONE) ? 'phone' : 'computer', site: brand.id,
        opening_at: opening.shownAt,
        ...(res.variant ? { variant: res.variant } : {}), ...offerMeasures(took[0]), ...contactTestProps(opening),
      })
      return opening
    },
  }
}
