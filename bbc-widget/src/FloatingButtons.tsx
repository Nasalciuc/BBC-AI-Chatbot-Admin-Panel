interface Props {
  onSelect: (tunnel: 'sales' | 'support') => void
  showAttention?: boolean
}

export function FloatingButtons({ onSelect, showAttention = false }: Props) {
  return (
    <div
      id="bbc-floating-btn"
      style={{
        position: 'fixed',
        bottom: 24,
        right: 24,
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
        zIndex: 2147483000,
      }}
    >
      {/* Badge attention — apare deasupra butonului principal */}
      {showAttention && (
        <div
          style={{
            position: 'absolute',
            bottom: 'calc(100% + 10px)',
            right: 0,
            background: '#0B1829',
            border: '1px solid #C9A54E',
            borderRadius: '20px',
            padding: '8px 16px',
            color: '#C9A54E',
            fontSize: '13px',
            fontWeight: 600,
            whiteSpace: 'nowrap',
            boxShadow: '0 4px 20px rgba(201,165,78,0.25)',
            cursor: 'pointer',
            animation: 'bbc-fadein 0.3s ease forwards',
            fontFamily: 'system-ui, -apple-system, sans-serif',
          }}
          onClick={() => onSelect('sales')}
        >
          ✈️ Ask our travel specialists
        </div>
      )}

      {/* Buton principal — Book Business Class */}
      <button
        onClick={() => onSelect('sales')}
        aria-label="Chat with sales"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '12px 20px',
          borderRadius: 50,
          background: '#C9A54E',
          color: '#fff',
          border: 'none',
          cursor: 'pointer',
          fontSize: 14,
          fontWeight: 600,
          boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
          animation: showAttention ? 'bbc-bounce 0.7s ease 2' : 'none',
          fontFamily: 'system-ui, -apple-system, sans-serif',
        }}
      >
        💬 Book Business Class
      </button>

      {/* Buton secundar — Support */}
      <button
        onClick={() => onSelect('support')}
        aria-label="Chat with support"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '10px 18px',
          borderRadius: 50,
          background: '#0B1829',
          color: '#fff',
          border: 'none',
          cursor: 'pointer',
          fontSize: 13,
          fontWeight: 500,
          boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
          fontFamily: 'system-ui, -apple-system, sans-serif',
        }}
      >
        🎧 Support
      </button>
    </div>
  )
}
