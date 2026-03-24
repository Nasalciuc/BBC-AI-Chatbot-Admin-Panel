interface Props {
  onSelect: (tunnel: 'sales' | 'support') => void
}

export function FloatingButtons({ onSelect }: Props) {
  return (
    <div style={{ position: 'fixed', bottom: 24, right: 24, display: 'flex', flexDirection: 'column', gap: 10, zIndex: 9999 }}>
      <button
        onClick={() => onSelect('sales')}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '12px 20px', borderRadius: 50,
          background: '#C9A54E', color: '#fff',
          border: 'none', cursor: 'pointer', fontSize: 14, fontWeight: 600,
          boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
        }}
      >
        💬 Book Business Class
      </button>
      <button
        onClick={() => onSelect('support')}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '10px 18px', borderRadius: 50,
          background: '#0B1829', color: '#fff',
          border: 'none', cursor: 'pointer', fontSize: 13, fontWeight: 500,
          boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
        }}
      >
        🎧 Support
      </button>
    </div>
  )
}
