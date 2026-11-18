import { useEffect } from 'preact/hooks'

/**
 * The on-screen keyboard over the chat (E2, ADR-12b, 18 Nov 2026): in the recordings of the Kayak card,
 * the phone's keyboard covered the box the visitor was typing in. On a phone the panel is a full-screen
 * sheet that already follows the visible area (phoneSheet.ts: --bw-vvh, --bw-vvt). Everywhere else with a
 * touch screen — a phone held sideways, a tablet, a large phone past the sheet's width — the panel floats
 * at the bottom of the layout viewport, which the keyboard does not shrink on iOS (nor on Android since
 * Chrome 108): the keyboard covered its lower part, message box included. Here the panel is lifted by the
 * keyboard's height (--bw-kb) and kept no taller than the visible area (--bw-avail), from visualViewport,
 * only while a keyboard is open (the root's class bw-kb-open).
 */

/** How much of the layout viewport the keyboard (or anything else) hides at the bottom, in px. */
export function keyboardInset(innerHeight: number, vvHeight: number, vvOffsetTop: number): number {
  if (!(innerHeight > 0) || !(vvHeight > 0)) return 0
  const hidden = innerHeight - vvHeight - Math.max(0, vvOffsetTop)
  // Under 80 px is the browser's own bar moving, not a keyboard.
  return hidden >= 80 ? Math.round(hidden) : 0
}

/** While `active`, keeps --bw-kb and --bw-avail on the widget's root current with the keyboard. */
export function useKeyboardInset(active: boolean) {
  useEffect(() => {
    if (!active) return
    const vv = typeof window !== 'undefined' ? window.visualViewport : null
    const root = typeof document !== 'undefined' ? document.getElementById('bbc-widget-root') : null
    if (!vv || !root) return
    let coarse = false
    try { coarse = window.matchMedia('(pointer: coarse)').matches } catch { coarse = false }
    if (!coarse) return
    const fit = () => {
      const kb = keyboardInset(window.innerHeight, vv.height, vv.offsetTop)
      root.style.setProperty('--bw-kb', `${kb}px`)
      root.style.setProperty('--bw-avail', `${Math.round(vv.height)}px`)
      // Only while a keyboard is open does the panel move (styles.ts, .bw-kb-open).
      root.classList.toggle('bw-kb-open', kb > 0)
    }
    fit()
    vv.addEventListener('resize', fit)
    vv.addEventListener('scroll', fit)
    return () => {
      vv.removeEventListener('resize', fit)
      vv.removeEventListener('scroll', fit)
      root.style.removeProperty('--bw-kb')
      root.style.removeProperty('--bw-avail')
      root.classList.remove('bw-kb-open')
    }
  }, [active])
}
