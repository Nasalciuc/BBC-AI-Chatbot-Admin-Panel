/**
 * Watches the site's quote window and says when one is left without a send (quoteWindowRead.ts says why and how).
 *
 * The window is the quote form's modal: the form (form.qf, or one labelled as a quote) inside a dialog, or inside the
 * floating box that holds the window's own × (`[data-qd-close]`, buybusinessclass.com, Nov 2026), or inside something
 * fixed over the page. A quote form in the page itself is no window. The page is looked at every WINDOW_LOOK_MS: a
 * window seen open starts a run; the run ends when it is no longer shown, or when the page goes out of view with it
 * open. Each run that ends without a send is one `quote_window_closed` (PostHog) and one call to the listeners
 * (proactive.ts: the form_left moment of test 1). Never in the CRM's iframe (entry.tsx).
 */
import brand from './config'
import { posthog } from './posthog'
import {
  closeHow, closedFromRun, closedProps, newRun, runAfterSiteEvent, WINDOW_LOOK_MS,
  type CloseGesture, type CloseHow, type QuoteWindowClosed, type WindowRun,
} from './quoteWindowRead'
import { onSiteEvent, type SiteEvent } from './siteSignals'

const CLOSE = '[data-qd-close]'
const MODAL = 'dialog, [role="dialog"], [aria-modal="true"]'

/** A form the site asks for a quote with: form.qf on buybusinessclass.com, or one labelled as a quote. */
export function quoteForm(el: Element | null): HTMLFormElement | null {
  const form = el?.closest('form') as HTMLFormElement | null
  if (!form) return null
  if (form.classList.contains('qf')) return form
  return /quote|offer/i.test(form.getAttribute('aria-label') || '') ? form : null
}

function fixed(el: Element): boolean {
  try { return window.getComputedStyle(el).position === 'fixed' } catch { return false }
}

function floating(el: Element): boolean {
  for (let a: Element | null = el; a && a !== document.body && a !== document.documentElement; a = a.parentElement) {
    if (fixed(a)) return true
  }
  return false
}

/** The window a quote form sits in, or null for a form in the page itself. */
function windowOf(form: HTMLFormElement): Element | null {
  const modal = form.closest(MODAL)
  if (modal) return modal
  for (let el = form.parentElement; el && el !== document.body && el !== document.documentElement; el = el.parentElement) {
    // The box that holds the window's own ×, when it floats over the page; a page section that happens to hold the
    // modal's × somewhere inside it does not float, and is no window.
    if (el.querySelector(CLOSE)) return floating(el) ? el : null
    if (fixed(el)) return el
  }
  return null
}

function shown(el: Element): boolean {
  try {
    if (!el.isConnected) return false
    const r = el.getBoundingClientRect()
    if (r.width < 2 || r.height < 2) return false
    const cs = window.getComputedStyle(el)
    return cs.display !== 'none' && cs.visibility !== 'hidden' && Number(cs.opacity || '1') > 0.05
  } catch {
    return false
  }
}

/** The quote window open on the page now, or null. */
function openWindow(): Element | null {
  try {
    for (const form of Array.from(document.querySelectorAll('form'))) {
      if (form.closest('#bbc-widget-root') || !quoteForm(form)) continue
      const w = windowOf(form)
      if (w && shown(w)) return w
    }
  } catch { /* a page it cannot read has no window */ }
  return null
}

type Listener = (c: QuoteWindowClosed) => void
const listeners = new Set<Listener>()
let last: QuoteWindowClosed | null = null
let detach: (() => void) | null = null

/** Every window left from now on (proactive.ts); returns the way to stop listening. */
export function onQuoteWindowClosed(l: Listener): () => void {
  listeners.add(l)
  return () => { listeners.delete(l) }
}

/** The last window left on this page, or null. */
export function lastQuoteWindowClosed(): QuoteWindowClosed | null {
  return last
}

/** Starts watching (once per page; a second call starts over). */
export function startQuoteWindow(): void {
  stopQuoteWindow()
  let open: { el: Element; run: WindowRun } | null = null

  const finish = (how: CloseHow, at: number) => {
    const o = open
    open = null
    if (!o) return
    const c = closedFromRun(o.run, how, at)
    if (!c) return
    last = c
    posthog?.capture('quote_window_closed', closedProps(c, brand.id))
    for (const l of [...listeners]) {
      try { l(c) } catch { /* one listener's error never stops the others */ }
    }
  }
  const look = () => {
    const now = Date.now()
    if (open) {
      if (!shown(open.el)) finish(closeHow(open.run.gesture, now), now)
      return
    }
    if (document.visibilityState !== 'visible') return
    const el = openWindow()
    if (el) open = { el, run: newRun(now) }
  }
  const inOpen = (t: EventTarget | null) => !!open && t instanceof Node && open.el.contains(t)
  const inWidget = (t: EventTarget | null) => t instanceof Element && !!t.closest('#bbc-widget-root')
  const isField = (t: EventTarget | null) =>
    t instanceof HTMLElement && (t.matches('input, textarea, select') || t.isContentEditable)
  const gesture = (kind: CloseGesture) => { if (open) open.run = { ...open.run, gesture: { kind, at: Date.now() } } }

  const onPointer = (e: Event) => {
    if (!open || inWidget(e.target)) return
    const el = e.target instanceof Element ? e.target : null
    if (el && inOpen(el) && el.closest(CLOSE)) gesture('close_button')
    else if (!inOpen(e.target)) gesture('backdrop')
  }
  const onKey = (e: KeyboardEvent) => { if (open && e.key === 'Escape') gesture('escape') }
  const onFocus = (e: Event) => { if (open && inOpen(e.target) && isField(e.target)) open.run = { ...open.run, focused: true } }
  const onInput = (e: Event) => { if (open && inOpen(e.target)) open.run = { ...open.run, typed: true, focused: true } }
  const onInvalid = (e: Event) => { if (open && inOpen(e.target)) open.run = { ...open.run, refused: true } }
  const onSubmit = (e: Event) => { if (open && inOpen(e.target)) open.run = { ...open.run, sent: true } }
  const onSite = (e: SiteEvent) => {
    if (e.name === 'quote_form_opened') look()
    if (open) open.run = runAfterSiteEvent(open.run, e.name)
  }
  const onHidden = () => {
    if (document.visibilityState !== 'visible' && open) finish('page_hidden', Date.now())
  }
  const onPageHide = () => { if (open) finish('page_hidden', Date.now()) }

  const cap: AddEventListenerOptions = { capture: true, passive: true }
  document.addEventListener('pointerdown', onPointer, cap)
  document.addEventListener('click', onPointer, cap)
  document.addEventListener('keydown', onKey, cap)
  document.addEventListener('focusin', onFocus, cap)
  document.addEventListener('input', onInput, cap)
  document.addEventListener('invalid', onInvalid, cap)
  document.addEventListener('submit', onSubmit, cap)
  document.addEventListener('visibilitychange', onHidden)
  window.addEventListener('pagehide', onPageHide)
  const offSite = onSiteEvent(onSite)
  const tick = setInterval(look, WINDOW_LOOK_MS)
  look()
  detach = () => {
    clearInterval(tick)
    offSite()
    document.removeEventListener('pointerdown', onPointer, cap)
    document.removeEventListener('click', onPointer, cap)
    document.removeEventListener('keydown', onKey, cap)
    document.removeEventListener('focusin', onFocus, cap)
    document.removeEventListener('input', onInput, cap)
    document.removeEventListener('invalid', onInvalid, cap)
    document.removeEventListener('submit', onSubmit, cap)
    document.removeEventListener('visibilitychange', onHidden)
    window.removeEventListener('pagehide', onPageHide)
  }
}

/** Stops watching (the widget's teardown): nothing of the page stays. */
export function stopQuoteWindow(): void {
  detach?.()
  detach = null
  last = null
}
