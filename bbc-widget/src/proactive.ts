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
 * tab's session); 24 h of quiet after "Not now" or ✕; only with the page in view.
 *
 * Measurements (PostHog, no personal data): proactive_eligible (any group, also with the feature off —
 * the shadow mode; `moment_passed` says why an answer that came after the visitor moved on was not
 * shown), proactive_shown, proactive_engaged, proactive_dismissed, and
 * proactive_quote_unrecognized when a "Get free quote" button sits outside a form we recognise (the
 * site's HTML changed: the quote moment would otherwise die silently).
 */
import { useEffect, useRef, useState } from 'preact/hooks'
import { ensureVisitorId, fetchProactiveOffer } from './api'
import brand from './config'
import { posthog } from './posthog'
import {
  applySiteEvent, lastSearch, noMomentPage, onSiteEvent, siteEvents, sitePage, type SiteEvent,
} from './siteSignals'
import { hasContext, pickTrip, tripFromFields, tripFromTitle, tripFromUrl, type TripPayload } from './tripContext'
import {
  OFFER_TIMEOUT_MS, PAUSE_AFTER_DISMISS_MS, blockedPath, pauseRunning, pickTrigger, replyKind,
  type OpeningTrigger, type PageState, type QuoteState, type Trigger,
} from './triggers'
import { getUtm } from './utm'

export type Surface = 'card' | 'panel'

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
  /** Which way the server asked the first question (the note's bandit, app/services/note_bandit.py):
   *  sent back with the answer so the conversation starts with the same words and is counted for it. */
  variant?: string
}

// sessionStorage: this visit's moment was used ('asked', 'shown', or 'chat' when the visitor opened the
// chat themselves first).
const DONE_KEY = 'bbc_proactive_done'
// localStorage: no card until this time (24 h after "Not now" or ✕).
const PAUSE_KEY = 'bbc_proactive_pause_until'
// sessionStorage: the 10 s note beside the button was closed with ✕ in this tab.
const NOTE_CLOSED_KEY = 'bbc_note_closed'
// localStorage: the conversation the card opened (it has no form behind it, so no visitor key).
const CONV_KEY = 'bbc_conv_proactive'
// sessionStorage: Widget.tsx's once-per-visit note; set when a card shows so the note never follows it.
const ATTENTION_KEY = 'bbc_attention_shown'

const PHONE = '(max-width: 767px)'
const QUOTE_TEXT = /get (?:a )?free quote/i

function ssGet(k: string): string | null { try { return sessionStorage.getItem(k) } catch { return null } }
function ssSet(k: string, v: string) { try { sessionStorage.setItem(k, v) } catch { /* storage off: the guard lives in memory only */ } }
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

/** Where the visitor came from, for the conversation the card opens (what the form sends today). */
export function proactiveMetadata(): Record<string, string> {
  return { ...(getUtm() as Record<string, string>), site: brand.id }
}

function kayakVisit(): boolean {
  return (getUtm().utm_source || '').toLowerCase() === 'kayak'
}

function pageTitle(): string {
  try { return document.querySelector('h1')?.textContent || '' } catch { return '' }
}

/** The trip the card and the note start from (tripContext.pickTrip), the newest word first: the search
 *  the visitor ran on the site, the address, what the page says it is about (bbc-signals), its title. */
function currentTrip(): TripPayload | null {
  const page = sitePage()
  const pageSource = page?.pageType === 'search' || page?.pageType === 'flight' ? 'search' : 'route_page'
  let search = ''
  try { search = location.search } catch { /* no address: the rest */ }
  return pickTrip(
    tripFromFields(lastSearch(), 'search'),
    tripFromUrl(search, kayakVisit()),
    tripFromFields(page?.trip, pageSource),
    tripFromTitle(pageTitle()),
  )
}

/** A form the site asks for a quote with: form.qf on buybusinessclass.com, or one labelled as a quote. */
function quoteForm(el: Element | null): HTMLFormElement | null {
  const form = el?.closest('form') as HTMLFormElement | null
  if (!form) return null
  if (form.classList.contains('qf')) return form
  return /quote|offer/i.test(form.getAttribute('aria-label') || '') ? form : null
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
}

export function useProactive({ apiUrl, step, allowed, restored = null }: {
  apiUrl: string
  step: string
  allowed: boolean
  /** The opening the panel came back with on this page (kept with it): its answer or dismissal is measured. */
  restored?: ProactiveOpening | null
}): Proactive {
  const [offer, setOffer] = useState<ProactiveOpening | null>(null)
  const [quiet, setQuiet] = useState(() => ssGet(DONE_KEY) === 'shown')
  const shownRef = useRef<ProactiveOpening | null>(restored)
  const engagedRef = useRef(false)
  const stepRef = useRef(step)
  stepRef.current = step

  // The visitor opened the chat themselves (the corner button) before any moment: not this visit.
  useEffect(() => {
    if (step !== 'buttons' && !ssGet(DONE_KEY)) ssSet(DONE_KEY, 'chat')
  }, [step])

  // The site says the visitor sent the quote request, or reached out by phone, WhatsApp or email
  // (bbc-signals: quote_form_submitted, contact_clicked): no moment and no 10 s note for the rest of the
  // visit — they are already talking to us. Read from what the page queued before the widget loaded too.
  useEffect(() => {
    const stopFor = (e: SiteEvent) => {
      const { stop } = applySiteEvent(null, e)
      if (!stop) return
      // A moment this visit already used keeps its word ('shown' keeps the next pages quiet too).
      if (!ssGet(DONE_KEY)) ssSet(DONE_KEY, stop)
      ssSet(ATTENTION_KEY, '1')
      // A note already waiting (or on screen) goes away too: Widget shows it only while not quiet.
      setQuiet(true)
      // So does a card still waiting for an answer (an answered one became the chat: `opened` cleared it).
      setOffer(null)
    }
    for (const e of siteEvents()) stopFor(e)
    return onSiteEvent(stopFor)
  }, [])

  useEffect(() => {
    if (!allowed || step !== 'buttons') return
    if (ssGet(DONE_KEY) || ssGet(NOTE_CLOSED_KEY) || pauseRunning(lsGet(PAUSE_KEY), Date.now())) return

    let disposed = false
    let fired = false
    let warnedShape = false
    let sentInline = false
    const start = Date.now()
    let visibleAcc = 0
    let visibleSince: number | null = document.visibilityState === 'visible' ? start : null
    let lastActivity = start
    let maxScroll = 0
    let lastSiteTyping: number | null = null
    let press: Press | null = null
    // The last time the visitor worked on the quote form, pressed or not (triggers.ts: no card over it).
    let quoteFormAt: number | null = null
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
      touchQuoteForm(e.target)
      if (press && inPressedForm(e.target)) press.activityAt = Date.now()
    }
    const onFormTouch = (e: Event) => {
      touchQuoteForm(e.target)
      if (press && inPressedForm(e.target)) press.activityAt = Date.now()
    }
    const onClick = (e: Event) => {
      lastActivity = Date.now()
      const el = e.target instanceof Element ? e.target : null
      if (!el || inWidget(el)) return
      touchQuoteForm(el)
      const form = quoteForm(el)
      if (form && isSubmitControl(el)) {
        press = { at: Date.now(), form, activityAt: Date.now(), invalid: false }
        return
      }
      if (press && inPressedForm(el)) {
        press.activityAt = Date.now()
        return
      }
      const control = el.closest('button, a, [role="button"], input[type="submit"]')
      const text = control ? (control.textContent || (control as HTMLInputElement).value || '') : ''
      if (control && QUOTE_TEXT.test(text)) {
        // The site's button without the form we know: still a press, and the HTML changed — say so.
        press = { at: Date.now(), form: null, activityAt: Date.now(), invalid: false }
        if (!warnedShape) {
          warnedShape = true
          posthog?.capture('proactive_quote_unrecognized', { path: location.pathname, site: brand.id })
        }
      }
    }
    // Enter in a field sends the form too.
    const onSubmit = (e: Event) => {
      const form = e.target instanceof HTMLFormElement ? quoteForm(e.target) : null
      if (form) press = { at: Date.now(), form, activityAt: Date.now(), invalid: press?.invalid ?? false }
    }
    // The browser refused the send and flagged a field (`invalid` does not bubble: caught on the way down).
    const onInvalid = (e: Event) => {
      if (!press || !inPressedForm(e.target)) return
      press.invalid = true
      press.activityAt = Date.now()
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
      if (e.name === 'quote_form_opened' || e.name === 'quote_form_step' || e.name === 'quote_form_invalid'
        || e.name === 'quote_form_submitting' || e.name === 'quote_form_failed') {
        quoteFormAt = Math.max(quoteFormAt ?? 0, Math.min(e.at, Date.now()))
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
    }

    // What the moment was decided on. The server answers within OFFER_TIMEOUT_MS; by then the visitor
    // may have moved on, and an answer that comes after that is dropped (and measured), never shown.
    // `search`: the site's last search (each one a new object, siteSignals.cleanDetail): a new search on the
    // way makes the trip the answer was asked with an old one.
    const seen = () => ({
      typing: lastSiteTyping, pressAt: press?.at ?? null, formAt: press?.activityAt ?? null, search: lastSearch(),
    })
    const momentPassed = (trigger: Trigger, before: ReturnType<typeof seen>): string | null => {
      if (stopped) return stopped
      if (stepRef.current !== 'buttons') return 'chat_opened'
      if (document.visibilityState !== 'visible') return 'hidden'
      if (ssGet(NOTE_CLOSED_KEY)) return 'note_closed'
      if (blockedPath(location.pathname)) return 'left_page'
      // The site moved on to its thank-you or a legal page while the answer was on its way.
      if (noMomentPage(sitePage())) return 'left_page'
      if (lastSiteTyping !== before.typing) return 'typing'
      if ((press?.at ?? null) !== before.pressAt || (press?.activityAt ?? null) !== before.formAt) return 'quote_form'
      if (lastSearch() !== before.search) return 'searched'
      if (trigger === 'quote' && press?.form && (!press.form.isConnected || sending(press.form))) return 'sent'
      return null
    }

    const ask = async (trigger: Trigger, phone: boolean) => {
      const before = seen()
      const trip = currentTrip()
      const device = phone ? 'phone' : 'computer'
      const res = await fetchProactiveOffer(
        apiUrl, { visitor_id: ensureVisitorId(), trigger, trip, site: brand.id }, OFFER_TIMEOUT_MS,
      )
      const passed = !disposed && res?.show && res.message ? momentPassed(trigger, before) : null
      // `site` on every proactive_* event (ADR 18): BBC and BCT are compared, never mixed.
      posthog?.capture('proactive_eligible', {
        trigger, arm: res ? res.arm : 'no_answer', device, has_context: hasContext(trip), site: brand.id,
        // The page carries bbc-signals: the moments with the site's word against those without it.
        site_signals: !!sitePage(),
        ...(passed ? { moment_passed: passed } : {}),
      })
      if (disposed || !res || !res.show || !res.message || passed) return
      const surface: Surface = phone ? 'card' : 'panel'
      const opening: ProactiveOpening = {
        trigger, message: res.message, quickReplies: res.quick_replies, trip, surface, shownAt: Date.now(),
        ...(res.variant ? { variant: res.variant } : {}),
      }
      ssSet(DONE_KEY, 'shown')
      ssSet(ATTENTION_KEY, '1')
      shownRef.current = opening
      setQuiet(true)
      setOffer(opening)
      posthog?.capture('proactive_shown', { trigger, surface, device, site: brand.id })
    }

    const evaluate = () => {
      if (fired || disposed || stopped || document.visibilityState !== 'visible') return
      // The note closed with ✕ while this page was open: no card for the rest of the visit.
      if (ssGet(NOTE_CLOSED_KEY)) return
      // The site says this is its thank-you or a legal page (bbc-signals), whatever the address.
      if (noMomentPage(sitePage())) return
      // The quote form went away after the press: it was sent in place. Nothing on this page after that.
      if (press?.form && !press.form.isConnected) {
        sentInline = true
        press = null
      }
      if (sentInline) return
      const now = Date.now()
      const quote: QuoteState | null = press ? {
        sincePressS: (now - press.at) / 1000,
        sinceFormActivityS: (now - Math.max(press.at, press.activityAt)) / 1000,
        invalid: press.invalid,
        busy: sending(press.form) || siteBusy,
      } : null
      const state: PageState = {
        path: location.pathname,
        kayak: kayakVisit(),
        visibleS: visibleS(),
        sinceActivityS: (now - lastActivity) / 1000,
        scrolledScreens: maxScroll / Math.max(1, window.innerHeight || 1),
        phone: matches(PHONE),
        sinceSiteTypingS: lastSiteTyping === null ? null : (now - lastSiteTyping) / 1000,
        quote,
        sinceQuoteFormS: quoteFormAt === null ? null : (now - quoteFormAt) / 1000,
        exitIntent: now - exitAt < 1500,
      }
      const trigger = pickTrigger(state)
      if (!trigger) return
      fired = true
      ssSet(DONE_KEY, 'asked')
      void ask(trigger, state.phone)
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
    const tick = setInterval(evaluate, 1000)

    return () => {
      disposed = true
      clearInterval(tick)
      offSite()
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
  }, [allowed, step, apiUrl])

  return {
    offer,
    quiet,
    opened: () => setOffer(null),
    dismiss: (how) => {
      lsSet(PAUSE_KEY, String(Date.now() + PAUSE_AFTER_DISMISS_MS))
      const o = shownRef.current
      if (o) {
        posthog?.capture('proactive_dismissed', {
          trigger: o.trigger, how, surface: o.surface, seconds_visible: Math.round((Date.now() - o.shownAt) / 1000),
          site: brand.id, ...(o.variant ? { variant: o.variant } : {}),
        })
      }
      setOffer(null)
    },
    engaged: (reply) => {
      const o = shownRef.current
      if (!o || engagedRef.current) return
      engagedRef.current = true
      posthog?.capture('proactive_engaged', {
        trigger: o.trigger,
        surface: o.surface,
        reply: replyKind(reply, o.quickReplies),
        seconds_to_reply: Math.round((Date.now() - o.shownAt) / 1000),
        site: brand.id,
        ...(o.variant ? { variant: o.variant } : {}),
      })
    },
    noteOpening: async (stillWanted, door = 'note') => {
      const trip = currentTrip()
      const res = await fetchProactiveOffer(
        apiUrl, { visitor_id: ensureVisitorId(), trigger: 'note', trip, site: brand.id }, OFFER_TIMEOUT_MS,
      )
      if (!res || !res.show || !res.message) return null
      // The visitor did something else while the words were on their way (Widget's note turn): they are
      // not shown, so nothing says they were — no proactive_shown, and the cards of this visit stay possible.
      if (stillWanted && !stillWanted()) return null
      const opening: ProactiveOpening = {
        trigger: 'note', message: res.message, quickReplies: res.quick_replies, trip, surface: 'panel',
        shownAt: Date.now(),
        ...(res.variant ? { variant: res.variant } : {}),
      }
      // The visitor opened the chat: no card follows in this visit (the moments' guard reads this).
      ssSet(DONE_KEY, 'chat')
      shownRef.current = opening
      engagedRef.current = false
      // `variant`: the note's bandit compares its two first questions from the tap on, not only from the
      // chats that started (the server counts those).
      posthog?.capture('proactive_shown', {
        trigger: 'note', surface: 'panel', door, device: matches(PHONE) ? 'phone' : 'computer', site: brand.id,
        ...(res.variant ? { variant: res.variant } : {}),
      })
      return opening
    },
  }
}
