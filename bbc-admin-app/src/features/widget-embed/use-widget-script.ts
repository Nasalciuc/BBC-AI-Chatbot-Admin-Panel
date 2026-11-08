import { useEffect, useState } from 'react'
import type { WidgetScriptState } from '@/lib/types'

const API_URL =
  import.meta.env.VITE_API_URL ||
  'https://admin-panel-error-production.up.railway.app'

/** Drop the widget this page holds: unmount it first through the bundle's own
 *  teardown, so its cleanups run (a phone sheet gives the page its scroll and
 *  theme colour back, the chat's stream and timers stop), then the root. */
function dropWidget() {
  window.__BBC_WIDGET_TEARDOWN__?.()
  document.getElementById('bbc-widget-root')?.remove()
  window.__BBC_WIDGET_LOADED__ = false
}

/**
 * Loads the real chat widget into this page: /widget/bbc-widget.js, built from
 * bbc-widget/src at every deploy (scripts/sync-widget.cjs), the same bundle
 * buybusinessclass.com loads. The panel keeps no copy of the widget of its own.
 *
 * `embedded`: no corner button, the chat fills the page (the CRM iframe).
 *
 * Returns where the script stands, so the page can say so when it failed: a
 * request that errors, or a load that ran no widget (no root afterwards — a
 * missing bundle can come back as the app's own HTML), is "failed".
 */
export function useWidgetScript({ embedded }: { embedded: boolean }): WidgetScriptState {
  const [state, setState] = useState<WidgetScriptState>('loading')

  useEffect(() => {
    // A widget still mounted (React StrictMode's double effect, another widget page)
    dropWidget()
    setState('loading')

    const script = document.createElement('script')
    script.src = '/widget/bbc-widget.js'
    script.setAttribute('data-api', API_URL)
    if (embedded) script.setAttribute('data-mode', 'embedded')
    script.async = true
    // The bundle renders while it runs, and `load` fires after that.
    const onLoad = () => setState(document.getElementById('bbc-widget-root') ? 'ready' : 'failed')
    const onError = () => setState('failed')
    script.addEventListener('load', onLoad)
    script.addEventListener('error', onError)
    document.body.appendChild(script)

    return () => {
      script.removeEventListener('load', onLoad)
      script.removeEventListener('error', onError)
      script.remove()
      dropWidget()
    }
  }, [embedded])

  return state
}
