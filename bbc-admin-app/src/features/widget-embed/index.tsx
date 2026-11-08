/**
 * Widget embed page: the real widget (use-widget-script.ts), nothing else.
 * The CRM loads it in an iframe as /widget-embed?embedded=1 → open panel, no bubble.
 *
 * To update the widget: edit bbc-widget/src/; every deploy builds it into
 * public/widget/bbc-widget.js (scripts/sync-widget.cjs).
 */
import { useWidgetScript } from './use-widget-script'
import { WidgetFailed } from './widget-failed'

export default function WidgetEmbed() {
  const state = useWidgetScript({
    embedded: new URLSearchParams(window.location.search).get('embedded') === '1',
  })

  // Empty page — widget renders itself into #bbc-widget-root; if it can't, say so
  return (
    <div style={{ width: '100vw', height: '100vh', background: '#f9fafb' }}>
      {state === 'failed' && <WidgetFailed />}
    </div>
  )
}
