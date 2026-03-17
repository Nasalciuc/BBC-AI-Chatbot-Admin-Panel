import { BBCChatWidget } from '../widget-preview/components/bbc-chat-widget'

export default function WidgetEmbed() {
  return (
    <div style={{
      width: '100vw',
      height: '100vh',
      overflow: 'hidden',
      margin: 0,
      padding: 0,
      background: '#fff',
    }}>
      <BBCChatWidget />
    </div>
  )
}
