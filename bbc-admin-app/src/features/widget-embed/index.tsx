import { BBCChatWidget } from '../widget-preview/components/bbc-chat-widget'

export default function WidgetEmbed() {
  const params = new URLSearchParams(window.location.search)
  const tunnel = params.get('tunnel') === 'support' ? 'support' as const : 'sales' as const

  return (
    <div style={{
      width: '100vw',
      height: '100vh',
      overflow: 'hidden',
      margin: 0,
      padding: 0,
      background: '#fff',
    }}>
      <BBCChatWidget tunnel={tunnel} />
    </div>
  )
}
