/**
 * Watches the site's quote window and says when one is left without a send (quoteWindowRead.ts says why and how).
 *
 * The window is the quote form's modal: the form (form.qf, or one labelled as a quote) inside a dialog, or inside the
 * floating box that holds the window's own × (`[data-qd-close]`, buybusinessclass.com, Nov 2026), or inside something
 * fixed over the page. A quote form in the page itself is no window. The page is looked at every WINDOW_LOOK_MS: a
 * window seen open starts a run; the run ends when it is no longer shown, or when the page goes out of view with it
 * open (one a send went out of waits for the page to come back, `kept`). Each run that ends without a send is one
 * `quote_window_closed` (PostHog) and one call to the listeners
 * (proactive.ts: the form_left moment of test 1). Never in the CRM's iframe (entry.tsx).
 */
import brand from './config'
import { posthog } from './posthog'
import {
  closeHow, closedFromRun, closedProps, keptAfterSiteEvent, newRun, runAfterSiteEvent, WINDOW_LOOK_MS,
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

/** The window a quote form sits in, and the box in it that holds the form (boxOf), or null for a form in the page
 *  itself. */
function windowOf(form: HTMLFormElement): { el: Element; box: Element } | null {
  const modal = form.closest(MODAL)
  if (modal) return { el: modal, box: boxOf(form, modal) }
  for (let el = form.parentElement; el && el !== document.body && el !== document.documentElement; el = el.parentElement) {
    // The box that holds the window's own ×, when it floats over the page; a page section that happens to hold the
    // modal's × somewhere inside it does not float, and is no window. That box is the window's panel itself.
    if (el.querySelector(CLOSE)) return floating(el) ? { el, box: el } : null
    // Something fixed over the page may cover the whole screen and hold its backdrop too, as a dialog may.
    if (fixed(el)) return { el, box: boxOf(form, el) }
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

/** `el` covers (nearly) the whole screen, as a backdrop does. */
function coversScreen(el: Element): boolean {
  try {
    const r = el.getBoundingClientRect()
    return r.width >= window.innerWidth * 0.9 && r.height >= window.innerHeight * 0.9
  } catch {
    return false
  }
}

/** The box inside the window that holds the form: the nearest one that also holds the window's ×, else the window's
 *  own part that holds the form. A dialog that covers the whole screen holds its backdrop too (CodeRabbit #291): a
 *  press inside the dialog but outside this box is on the backdrop. The window itself when nothing narrower holds it. */
function boxOf(form: HTMLFormElement, w: Element): Element {
  // A dialog the size of a panel is the panel: only one that covers the screen holds a backdrop of its own.
  if (!coversScreen(w)) return w
  let part: Element = form
  for (let el = form.parentElement; el && el !== w; el = el.parentElement) {
    if (el.querySelector(CLOSE)) return el
    part = el
  }
  return part
}

/** The quote window open on the page now (and the box in it that holds the form), or null. */
function openWindow(): { el: Element; box: Element } | null {
  try {
    for (const form of Array.from(document.querySelectorAll('form'))) {
      if (form.closest('#bbc-widget-root') || !quoteForm(form)) continue
      const w = windowOf(form)
      if (w && shown(w.el)) return w
    }
  } catch { /* a page it cannot read has no window */ }
  return null
}

/** A press outside `box`, by where the pointer was: a click the keyboard made (no pointer, `detail` 0) is nowhere. */
function outside(e: Event, box: Element): boolean {
  if (!(e instanceof MouseEvent) || (e.type === 'click' && e.detail === 0)) return false
  try {
    const r = box.getBoundingClientRect()
    return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom
  } catch {
    return false
  }
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
  let open: { el: Element; box: Element; run: WindowRun } | null = null
  // The run of a window a send went out of, when the page went out of view with it still open, and when it went
  // (CodeRabbit #291). Back in view, the same window still shown goes on with that run, so its close is no window left
  // while the send stands. The site's word meanwhile is the run's (keptAfterSiteEvent): a send that fails while the page
  // is out of view leaves the window there unsent, as any other. Taken up by the run that goes on, so a failure after
  // that is the run's alone; forgotten once the window is no longer shown: the next time it shows, it is a new quote.
  let kept: { el: Element; box: Element; run: WindowRun; at: number } | null = null

  const report = (run: WindowRun, how: CloseHow, at: number) => {
    const c = closedFromRun(run, how, at)
    if (!c) return
    last = c
    posthog?.capture('quote_window_closed', closedProps(c, brand.id))
    for (const l of [...listeners]) {
      try { l(c) } catch { /* one listener's error never stops the others */ }
    }
  }
  const finish = (how: CloseHow, at: number) => {
    const o = open
    open = null
    if (!o) return
    if (how === 'page_hidden' && o.run.sent) {
      kept = { ...o, at, run: { ...o.run, gesture: null } }
      return
    }
    report(o.run, how, at)
  }
  const look = () => {
    const now = Date.now()
    if (open) {
      if (!shown(open.el)) finish(closeHow(open.run.gesture, now), now)
      return
    }
    if (document.visibilityState !== 'visible') return
    if (kept && !shown(kept.el)) kept = null
    const w = openWindow()
    if (!w) return
    let run = newRun(now)
    if (kept?.el === w.el) {
      run = kept.run
      kept = null
    }
    open = { ...w, run }
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
    else if (!inOpen(e.target) || outside(e, open.box)) gesture('backdrop')
  }
  const onKey = (e: KeyboardEvent) => { if (open && e.key === 'Escape') gesture('escape') }
  const onFocus = (e: Event) => { if (open && inOpen(e.target) && isField(e.target)) open.run = { ...open.run, focused: true } }
  const onInput = (e: Event) => { if (open && inOpen(e.target)) open.run = { ...open.run, typed: true, focused: true } }
  const onInvalid = (e: Event) => { if (open && inOpen(e.target)) open.run = { ...open.run, refused: true } }
  const onSubmit = (e: Event) => { if (open && inOpen(e.target)) open.run = { ...open.run, sent: true } }
  const onSite = (e: SiteEvent) => {
    if (e.name === 'quote_form_opened') look()
    if (open) open.run = runAfterSiteEvent(open.run, e.name)
    else if (kept) {
      const k = keptAfterSiteEvent(kept.run, e.name, document.visibilityState === 'visible')
      if (k.ended) {
        const at = kept.at
        kept = null
        report(k.run, 'page_hidden', at)
      } else kept = { ...kept, run: k.run }
    }
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
