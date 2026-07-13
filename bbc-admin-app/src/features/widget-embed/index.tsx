/**
 * Widget embed test page.
 * Loads the compiled bbc-widget.js IIFE — the same bundle used on buybusinessclass.com.
 * This ensures /widget-embed always tests the real production widget code.
 *
 * To update the widget: edit bbc-widget/src/ → npm run build → copy dist/bbc-widget.js
 */
import { useEffect } from 'react'

const API_URL =
  import.meta.env.VITE_API_URL ||
  'https://admin-panel-error-production.up.railway.app'

export default function WidgetEmbed() {
  useEffect(() => {
    // Clean up any previous widget instance (handles React StrictMode double-invoke)
    document.getElementById('bbc-widget-root')?.remove()
    ;(window as any).__BBC_WIDGET_LOADED__ = false

    const script = document.createElement('script')
    script.src = '/widget/bbc-widget.js'
    script.setAttribute('data-api', API_URL)
    // CRM iframe: /widget-embed?embedded=1 → open panel, no bubble
    if (new URLSearchParams(window.location.search).get('embedded') === '1') {
      script.setAttribute('data-mode', 'embedded')
    }
    script.async = true
    document.body.appendChild(script)

    return () => {
      script.remove()
      document.getElementById('bbc-widget-root')?.remove()
      ;(window as any).__BBC_WIDGET_LOADED__ = false
    }
  }, [])

  // Empty page — widget renders itself into #bbc-widget-root
  return (
    <div style={{ width: '100vw', height: '100vh', background: '#f9fafb' }} />
  )
}
