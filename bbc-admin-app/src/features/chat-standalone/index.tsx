import { useWidgetScript } from '../widget-embed/use-widget-script'
import { WidgetFailed } from '../widget-embed/widget-failed'

/**
 * /chat/sales and /chat/support: the real widget as a full page, open on its form
 * (embedded mode, as in the CRM). The widget has only the sales path since its
 * redesign (#230), so both addresses open the same chat; the route keeps
 * accepting both so old links still land here.
 */
export default function ChatStandalone() {
  const state = useWidgetScript({ embedded: true })

  // Empty page — in embedded mode the widget fills it (#bbc-widget-root); if it can't, say so
  return <div className='h-svh w-full bg-k-ffffff'>{state === 'failed' && <WidgetFailed />}</div>
}
