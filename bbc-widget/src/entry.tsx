import { render } from 'preact'
import { Widget } from './Widget'
import { captureUtm } from './utm'
import brand from './config'
import { WIDGET_CSS } from './styles'
import { setTurnstileSiteKey } from './turnstile'
import { TRANSCRIPT_EXPORT_V0 } from './api'
import { exposeRoutes } from './page-globals'
import './posthog'

// Guard: prevent double-init if script included twice
if ((window as any).__BBC_WIDGET_LOADED__) {
  // already loaded
} else {
  (window as any).__BBC_WIDGET_LOADED__ = true
  // Route map kept on the page (PR-15: TRANSCRIPT_EXPORT_V0 must stay in the
  // bundle; see api.ts). Nothing reads it; defined once per page, so a
  // remount after __BBC_WIDGET_TEARDOWN__ doesn't throw (page-globals.ts).
  // captureUtm runs below, once the script tag says whether attribution is
  // allowed (data-attribution).
  exposeRoutes(window, { transcriptExportV0: TRANSCRIPT_EXPORT_V0 })

  // Read API URL from script data-api attribute
  const script = document.currentScript as HTMLScriptElement | null
    || document.querySelector('script[src*="-widget"]') as HTMLScriptElement | null
  // data-attribution="off": the site loaded the chat for a visitor without marketing consent (they tapped the
  // chat button), so no click ids, campaign tags or Analytics id are kept (utm.ts). Without it, as before.
  captureUtm({ attribution: script?.getAttribute('data-attribution') !== 'off' })
  const apiUrl = script?.getAttribute('data-api')
    || 'https://admin-panel-error-production.up.railway.app'
  // PR-12: invisible Turnstile only when the site opts in with its key.
  setTurnstileSiteKey(script?.getAttribute('data-turnstile-sitekey'))

  // CRM/embedded mode: no floating bubble, panel open by default, fills the
  // iframe. Opt-in via script attr (data-mode="embedded" / data-embedded="1")
  // or page URL (?embedded=1). Never persisted to storage. OFF → site widget
  // behaves exactly as today.
  const embedded = script?.getAttribute('data-mode') === 'embedded'
    || script?.getAttribute('data-embedded') === '1'
    || (() => {
      try { return new URLSearchParams(location.search).get('embedded') === '1' }
      catch { return false }
    })()

  // The widget's stylesheet (src/styles.ts): scoped to #bbc-widget-root, with its own reset.
  if (!document.getElementById('bbc-widget-css')) {
    const style = document.createElement('style')
    style.id = 'bbc-widget-css'
    style.textContent = WIDGET_CSS
    document.head.appendChild(style)
  }

  // Create container — DOMContentLoaded fallback if body not ready
  function init() {
    if (!document.body) {
      document.addEventListener('DOMContentLoaded', init)
      return
    }
    const root = document.createElement('div')
    root.id = 'bbc-widget-root'
    document.body.appendChild(root)

    const s = root.style
    if (embedded) {
      // The iframe IS the widget's home — fill its viewport. position:fixed
      // + inset 0 works regardless of body height (html/body default to auto).
      s.position = 'fixed'
      s.top = '0'
      s.left = '0'
      s.width = '100%'
      s.height = '100%'
    }
    const c = brand.colors
    s.setProperty('--bw-ink', c.ink)
    s.setProperty('--bw-ink-rgb', c.inkRgb)
    s.setProperty('--bw-header-bg', c.headerBg)
    s.setProperty('--bw-header-rule', c.headerRule)
    s.setProperty('--bw-accent-on-ink', c.accentOnInk)
    s.setProperty('--bw-accent', c.accent)
    s.setProperty('--bw-cta', c.cta)
    s.setProperty('--bw-cta-text', c.ctaText)
    s.setProperty('--bw-cta-hover', c.ctaHover)
    s.setProperty('--bw-launcher', c.launcher)
    s.setProperty('--bw-launcher-ring', c.launcherRing)
    s.setProperty('--bw-launcher-ring-rgb', c.launcherRingRgb)

    render(<Widget apiUrl={apiUrl} embedded={embedded} />, root)

    // A page that mounts the widget and later drops it without a reload (the
    // panel's /widget-preview, /chat/* and /widget-embed are app routes) calls
    // this first. Unmounting runs every effect's cleanup: the phone sheet gives
    // the page its scroll and theme colour back, the chat's stream and timers
    // stop. Removing the root alone would leave all of that running.
    ;(window as any).__BBC_WIDGET_TEARDOWN__ = () => {
      render(null, root)
      root.remove()
      ;(window as any).__BBC_WIDGET_TEARDOWN__ = undefined
      ;(window as any).__BBC_WIDGET_LOADED__ = false
    }
  }
  init()
}
