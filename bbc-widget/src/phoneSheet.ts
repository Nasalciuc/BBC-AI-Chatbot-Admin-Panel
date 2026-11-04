import { useEffect } from 'preact/hooks'

const PHONE = '(max-width: 767px)'

/**
 * While the panel is open as a full-screen sheet on a phone:
 * - it fits the visible area above the on-screen keyboard (visualViewport), so the message box is never
 *   hidden behind the keyboard on iOS, where 100dvh ignores it;
 * - the page behind does not scroll under the visitor's thumb;
 * - the browser's own bar takes the panel's navy (theme-color), and gets its colour back on close.
 * Everything is restored exactly as found when the sheet closes.
 */
export function usePhoneSheet(open: boolean, embedded: boolean, ink: string) {
  useEffect(() => {
    if (!open || embedded) return
    let mq: MediaQueryList
    try { mq = window.matchMedia(PHONE) } catch { return }
    if (!mq.matches) return

    const root = document.getElementById('bbc-widget-root')
    const html = document.documentElement
    const body = document.body
    const before = { html: html.style.overflow, body: body.style.overflow }
    html.style.overflow = 'hidden'
    body.style.overflow = 'hidden'

    const metas = Array.from(document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]'))
    const metaBefore = metas.map(m => m.getAttribute('content'))
    let added: HTMLMetaElement | null = null
    if (metas.length) metas.forEach(m => m.setAttribute('content', ink))
    else {
      added = document.createElement('meta')
      added.name = 'theme-color'
      added.content = ink
      document.head.appendChild(added)
    }

    const vv = window.visualViewport
    const fit = () => {
      if (!vv || !root) return
      root.style.setProperty('--bw-vvh', `${Math.round(vv.height)}px`)
      root.style.setProperty('--bw-vvt', `${Math.round(vv.offsetTop)}px`)
    }
    fit()
    vv?.addEventListener('resize', fit)
    vv?.addEventListener('scroll', fit)

    return () => {
      html.style.overflow = before.html
      body.style.overflow = before.body
      metas.forEach((m, i) => {
        const c = metaBefore[i]
        if (c === null) m.removeAttribute('content')
        else m.setAttribute('content', c)
      })
      added?.remove()
      vv?.removeEventListener('resize', fit)
      vv?.removeEventListener('scroll', fit)
      root?.style.removeProperty('--bw-vvh')
      root?.style.removeProperty('--bw-vvt')
    }
  }, [open, embedded])
}
