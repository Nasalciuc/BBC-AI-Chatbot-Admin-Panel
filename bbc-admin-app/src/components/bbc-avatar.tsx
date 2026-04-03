/**
 * BBCAvatar — unified operator avatar component.
 * 4 states:
 *   1. URL + non-editable → image
 *   2. No URL + editable → dashed circle with gray X (Dan's placeholder)
 *   3. URL + editable → image with edit overlay on hover
 *   4. No URL + non-editable → colored initials (hash of name)
 */

interface BBCAvatarProps {
  name: string
  url?: string | null
  size?: number
  editable?: boolean
  onClick?: () => void
  className?: string
}

function getInitials(name: string): string {
  return name
    .split(' ')
    .map(w => w[0] || '')
    .join('')
    .toUpperCase()
    .slice(0, 2)
}

function getHue(name: string): number {
  return name.split('').reduce((acc, c) => acc + c.charCodeAt(0), 0) % 360
}

export function BBCAvatar({
  name,
  url,
  size = 32,
  editable = false,
  onClick,
  className = '',
}: BBCAvatarProps) {
  const initials = getInitials(name)
  const hue = getHue(name)

  const base: React.CSSProperties = {
    width: size,
    height: size,
    borderRadius: '50%',
    flexShrink: 0,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    overflow: 'hidden',
    cursor: editable ? 'pointer' : 'default',
  }

  // State 1: URL + non-editable → image
  if (url && !editable) {
    return (
      <img
        src={url}
        alt={name}
        style={{ ...base, objectFit: 'cover' }}
        className={className}
        onError={(e) => {
          const target = e.target as HTMLImageElement
          target.style.display = 'none'
        }}
      />
    )
  }

  // State 2: No URL + editable → dashed circle with X
  if (!url && editable) {
    return (
      <div
        onClick={onClick}
        title="Click to add profile photo URL"
        style={{
          ...base,
          border: '2px dashed #9ca3af',
          background: '#f9fafb',
        }}
        className={className}
      >
        <svg
          width={size * 0.38}
          height={size * 0.38}
          viewBox="0 0 24 24"
          fill="none"
        >
          <line x1="5" y1="5" x2="19" y2="19"
            stroke="#6b7280" strokeWidth="2.5" strokeLinecap="round" />
          <line x1="19" y1="5" x2="5" y2="19"
            stroke="#6b7280" strokeWidth="2.5" strokeLinecap="round" />
        </svg>
      </div>
    )
  }

  // State 3: URL + editable → image with edit overlay on hover
  if (url && editable) {
    return (
      <div
        onClick={onClick}
        title="Click to change photo"
        style={{ ...base, position: 'relative' }}
        className={`group ${className}`}
      >
        <img
          src={url}
          alt={name}
          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
        />
        <div style={{
          position: 'absolute', inset: 0, borderRadius: '50%',
          background: 'rgba(0,0,0,0.35)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: size * 0.3,
        }}
          className="opacity-0 group-hover:opacity-100 transition-opacity"
        >
          ✏️
        </div>
      </div>
    )
  }

  // State 4: No URL + non-editable → colored initials
  return (
    <div
      style={{
        ...base,
        background: `hsl(${hue}, 55%, 50%)`,
        color: '#fff',
        fontSize: size * 0.38,
        fontWeight: 600,
        letterSpacing: '-0.5px',
      }}
      className={className}
    >
      {initials}
    </div>
  )
}
