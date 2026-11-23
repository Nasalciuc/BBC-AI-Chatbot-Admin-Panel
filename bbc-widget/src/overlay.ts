/**
 * A popup of the site is open over the page (E1, ADR-12a, 18 Nov 2026). In 9 of the 413 recordings with
 * the Kayak card, something of the site covered it: the "Save extra $100" popup right after the card (4),
 * the airport picker open over the whole screen. The site and the widget do not know about each other, and
 * on a phone there is no room for two layers — while one of the site's is open, the card waits, and comes
 * once it closes. The popup speaks of money exactly when the card says only an agent names a price.
 *
 * Until the site says when its popups open and close (ADR-12a: an event of its own, later), a popup is
 * read the general way: the topmost thing of the site at the middle of the screen sits in something fixed
 * that covers at least POPUP_SHARE of it. A fixed header or a cookie bar is smaller; the widget's own
 * elements never count; and what covers the screen for a whole minute is the page's own layout, not a popup
 * (watchPopups). `ignore` lets the quote moment look past the quote form's own modal.
 */

export const POPUP_SHARE = 0.3

export interface Box { left: number; top: number; width: number; height: number }

/** The share of the viewport `box` covers, 0..1. */
export function coveredShare(box: Box, vw: number, vh: number): number {
  if (!(vw > 0 && vh > 0)) return 0
  const w = Math.max(0, Math.min(box.left + box.width, vw) - Math.max(box.left, 0))
  const h = Math.max(0, Math.min(box.top + box.height, vh) - Math.max(box.top, 0))
  return (w * h) / (vw * vh)
}

/** The fixed things of the site that cover the screen now (see above). Never throws. */
export function coveringNow(): Element[] {
  const found: Element[] = []
  try {
    const vw = window.innerWidth
    const vh = window.innerHeight
    const points: [number, number][] = [[vw / 2, vh / 2], [vw / 2, vh * 0.3], [vw / 2, vh * 0.7]]
    for (const [x, y] of points) {
      const stack = typeof document.elementsFromPoint === 'function' ? document.elementsFromPoint(x, y) : []
      let el: Element | null = stack.find(e => !e.closest('#bbc-widget-root')) ?? null
      while (el && el !== document.body && el !== document.documentElement) {
        const cs = window.getComputedStyle(el)
        if (cs.position === 'fixed') {
          const shown = cs.visibility !== 'hidden' && cs.display !== 'none' && Number(cs.opacity || '1') > 0.05
          if (shown && coveredShare(el.getBoundingClientRect(), vw, vh) >= POPUP_SHARE && !found.includes(el)) {
            found.push(el)
          }
          break  // the first fixed ancestor decides for this point
        }
        el = el.parentElement
      }
    }
  } catch { /* a page it cannot read has no popup */ }
  return found
}

/** After this long covering the screen without a break, a fixed thing is the page's own layout (an app shell,
 *  a full-screen menu the site keeps open), not a popup the card should wait for. */
export const LAYOUT_AFTER_MS = 60_000

/** E1 after the card: a popup of the site that opens over a card already on screen (secret-site's "Save extra
 *  $100" at 5 s, the Kayak card at 2.5 s: 4 of the 413 recordings, and the sandbox run of 22 Nov) puts the card
 *  aside, into the corner button, until the popup closes or has covered the page for a minute (then it is the
 *  page's layout, and the card comes back over it). Each call looks once; `changed` hears each turn, never the
 *  same state twice. `ignore`: what is not a popup for this card, as for its moment (the quote card looks past
 *  the quote form's own modal, CodeRabbit #276). */
export function cardAsideWatch(
  popups: { open: (ignore?: (el: Element) => boolean) => boolean },
  changed: (aside: boolean) => void,
  ignore?: (el: Element) => boolean,
): () => void {
  let aside = false
  return () => {
    const now = popups.open(ignore)
    if (now === aside) return
    aside = now
    changed(now)
  }
}

/** A popup watcher for one page. `seen` is when each covering thing was first seen in an unbroken run. */
export function watchPopups(clock: () => number = Date.now): {
  open: (ignore?: (el: Element) => boolean, covering?: Element[]) => boolean
} {
  const seen = new Map<Element, number>()
  return {
    open: (ignore, covering) => {
      const now = clock()
      const found = covering ?? coveringNow()
      for (const el of Array.from(seen.keys())) if (!found.includes(el)) seen.delete(el)
      let open = false
      for (const el of found) {
        if (!seen.has(el)) seen.set(el, now)
        if (now - (seen.get(el) as number) < LAYOUT_AFTER_MS && !(ignore && ignore(el))) open = true
      }
      return open
    },
  }
}
