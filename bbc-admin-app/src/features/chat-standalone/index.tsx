import { BBCChatWidget } from '../widget-preview/components/bbc-chat-widget'

interface Props {
  tunnel: 'sales' | 'support'
}

export default function ChatStandalone({ tunnel }: Props) {
  return (
    <div style={{
      width: '100vw',
      height: '100vh',
      overflow: 'hidden',
      margin: 0,
      padding: 0,
      background: '#fff',
      display: 'flex',
      flexDirection: 'column',
    }}>
      <div style={{
        textAlign: 'center',
        fontSize: '12px',
        fontWeight: 600,
        padding: '6px 0',
        backgroundColor: tunnel === 'support' ? '#1e40af' : '#0B1829',
        color: '#fff',
        letterSpacing: '0.5px',
      }}>
        {tunnel === 'support' ? 'BBC Support Assistant' : 'BBC Sales Assistant'}
      </div>
      <div style={{ flex: 1, overflow: 'hidden' }}>
        <BBCChatWidget tunnel={tunnel} />
      </div>
    </div>
  )
}
