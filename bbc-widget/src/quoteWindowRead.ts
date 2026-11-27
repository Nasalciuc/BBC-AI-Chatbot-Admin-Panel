/**
 * The site's quote window (the "Get free quote" modal) left without a send: test 1's first measurement ("Testul 1:
 * plan de implementare", 27 Nov 2026, step 1). On 23-27 Nov, of 651 Kayak phone visitors who left the window without
 * typing, 45% closed it with × and stayed on the page ~10 s, and 55% left the page with it still open (median 8 s):
 * nothing measured either. `quote_window_closed` says how each window went, once per window, so the moment of test 1
 * (form_left) can be read on everyone who leaves it, card or no card.
 *
 * How it went (`how`): the visitor's gesture just before the window went — its own × (`[data-qd-close]`), Esc, a
 * click outside it — or nothing of theirs: the page took it away (`unmounted`), or the page itself went out of view
 * with the window open (`page_hidden`). Until the site says it (an event of its own through bbc-signals, asked of
 * Adrian), the widget reads the page: `source: 'dom'`. A detection that breaks (the site renames its ×) falls on
 * `unmounted`, after which no moment comes: a broken read holds the card back, it never sends one wrongly.
 *
 * What the visitor did in it: entered a field (`focused`), typed a character (`typed`, from the `input` event, never
 * its value), had a send refused (`refused`). A window from which a send went out is no window left: nothing.
 *
 * Pure (no imports, node-testable); quoteWindow.ts watches the page.
 */

export type CloseHow = 'close_button' | 'escape' | 'backdrop' | 'unmounted' | 'page_hidden'
export type CloseGesture = 'close_button' | 'escape' | 'backdrop'

/** The ways a visitor closes the window themselves: the only ones after which the form_left moment may come. */
export const CLOSED_BY_VISITOR: readonly CloseHow[] = ['close_button', 'escape', 'backdrop']

/** A gesture this long before the window went is what closed it; an older one is not (the page took it away). */
export const GESTURE_MS = 1500
/** How often the page is looked at for the window (quoteWindow.ts). */
export const WINDOW_LOOK_MS = 250

/** One window, from when it was seen open until it went. */
export interface WindowRun {
  openedAt: number
  focused: boolean
  typed: boolean
  refused: boolean
  /** A send went out of it: the site said it is sending or sent, or the browser let the form submit. */
  sent: boolean
  gesture: { kind: CloseGesture; at: number } | null
}

export function newRun(at: number): WindowRun {
  return { openedAt: at, focused: false, typed: false, refused: false, sent: false, gesture: null }
}

/** The run after the site's word about its quote form (siteSignals.ts): a refused or failed send is a refusal (and
 *  a failed one went nowhere); a send in flight or done is a send; a step is the visitor in it. */
export function runAfterSiteEvent(run: WindowRun, name: string): WindowRun {
  switch (name) {
    case 'quote_form_invalid': return { ...run, refused: true, focused: true }
    case 'quote_form_failed': return { ...run, refused: true, sent: false }
    case 'quote_form_submitting':
    case 'quote_form_submitted': return { ...run, sent: true }
    case 'quote_form_step': return { ...run, focused: true }
    default: return run
  }
}

/** The run quoteWindow.ts keeps when the page goes out of view with a send out of it, after the site's word while it
 *  waits (CodeRabbit #291): the run as runAfterSiteEvent leaves it, and `ended` when the send failed with the page
 *  still out of view: the window was left there unsent, as any window the page hides without a send. Back in view, a
 *  failure is the run's own: the same window, still shown, goes on with it. */
export function keptAfterSiteEvent(
  run: WindowRun, name: string, pageVisible: boolean,
): { run: WindowRun; ended: boolean } {
  const next = runAfterSiteEvent(run, name)
  return { run: next, ended: !pageVisible && !next.sent }
}

/** How the window went at `goneAt`: the visitor's last gesture when it came just before, else the page took it. */
export function closeHow(gesture: WindowRun['gesture'], goneAt: number): CloseHow {
  if (gesture && goneAt - gesture.at >= 0 && goneAt - gesture.at <= GESTURE_MS) return gesture.kind
  return 'unmounted'
}

export interface QuoteWindowClosed {
  how: CloseHow
  source: 'dom'
  /** When the window went (Date.now()). */
  at: number
  secondsOpen: number
  focused: boolean
  typed: boolean
  refused: boolean
}

/** The window left, or null when a send went out of it (closed after sending is no window left). */
export function closedFromRun(run: WindowRun, how: CloseHow, at: number): QuoteWindowClosed | null {
  if (run.sent) return null
  return {
    how, source: 'dom', at, secondsOpen: Math.max(0, Math.round((at - run.openedAt) / 100) / 10),
    focused: run.focused || run.typed, typed: run.typed, refused: run.refused,
  }
}

/** The PostHog event's properties: words and yes/no only, never what the visitor typed. */
export function closedProps(c: QuoteWindowClosed, site: string): Record<string, unknown> {
  return {
    how: c.how, source: c.source, seconds_open: c.secondsOpen,
    focused: c.focused, typed: c.typed, refused: c.refused, site,
  }
}
