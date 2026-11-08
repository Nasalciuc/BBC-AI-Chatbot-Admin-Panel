import { useEffect } from 'react'

declare global {
  interface Window {
    __BBC_WIDGET_LOADED__?: boolean
  }
}

const API_URL =
  import.meta.env.VITE_API_URL ||
  'https://admin-panel-error-production.up.railway.app'

/**
 * Loads the real chat widget into this page: /widget/bbc-widget.js, built from
 * bbc-widget/src at every deploy (scripts/sync-widget.cjs), the same bundle
 * buybusinessclass.com loads. The panel keeps no copy of the widget of its own.
 *
 * `embedded`: no corner button, the chat fills the page (the CRM iframe).
 */
export function useWidgetScript({ embedded }: { embedded: boolean }) {
  useEffect(() => {
    // Clean up any previous widget instance (handles React StrictMode double-invoke)
    document.getElementById('bbc-widget-root')?.remove()
    window.__BBC_WIDGET_LOADED__ = false

    const script = document.createElement('script')
    script.src = '/widget/bbc-widget.js'
    script.setAttribute('data-api', API_URL)
    if (embedded) script.setAttribute('data-mode', 'embedded')
    script.async = true
    document.body.appendChild(script)

    return () => {
      script.remove()
      document.getElementById('bbc-widget-root')?.remove()
      window.__BBC_WIDGET_LOADED__ = false
    }
  }, [embedded])
}
