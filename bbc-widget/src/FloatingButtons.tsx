import brand from './config'

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
        alignItems: 'center',
        gap: 12,
        zIndex: 2147483000,
      }}
    >
      {showAttention && (
        <div
          role="tooltip"
          onClick={() => onSelect('sales')}
          style={{
            position: 'relative',
            fontFamily: "'Inter', system-ui, -apple-system, sans-serif",
            fontSize: '11px',
            fontWeight: 500,
            color: '#4B5563',
            background: '#FFFFFF',
            padding: '6px 12px',
            borderRadius: '6px',
            boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
            whiteSpace: 'nowrap',
            cursor: 'pointer',
            animation: 'bbc-fadein 0.3s ease forwards',
          }}
        >
          New booking
          <span
            aria-hidden
            style={{
              position: 'absolute',
              right: -6,
              top: '50%',
              transform: 'translateY(-50%)',
              width: 0,
              height: 0,
              borderTop: '6px solid transparent',
              borderBottom: '6px solid transparent',
              borderLeft: '6px solid #FFFFFF',
            }}
          />
        </div>
      )}

      <button
        type="button"
        onClick={() => onSelect('sales')}
        aria-label="New booking"
        style={{
          width: 60,
          height: 60,
          borderRadius: '50%',
          background: '#0B1829',
          border: '2px solid #C9A54E',
          boxShadow: '0 4px 20px rgba(201,165,78,0.25)',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          padding: 0,
          flexShrink: 0,
          animation: showAttention ? 'bbc-bounce 0.7s ease 2' : 'none',
        }}
      >
        <img
          src={brand.logoUrl}
          alt=""
          width={30}
          height={30}
          draggable={false}
          style={{ display: 'block', pointerEvents: 'none' }}
        />
      </button>
    </div>
  )
}
