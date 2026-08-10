import { useState } from 'react'

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
  const [imgError, setImgError] = useState(false)
  const [prevUrl, setPrevUrl] = useState(url)

  // Reset error state when URL changes (so new valid URLs are tried)
  if (prevUrl !== url) {
    setPrevUrl(url)
    setImgError(false)
  }

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

  // State 1: URL + non-editable + no load error → image
  if (url && !editable && !imgError) {
    return (
      <img
        src={url}
        alt={name}
        style={{ ...base, objectFit: 'cover' }}
        className={className}
        onError={() => setImgError(true)}
      />
    )
  }
  // If imgError=true → falls through to State 4 (colored initials) ↓

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

  // State 3: URL + editable + no load error → image with edit overlay on hover
  if (url && editable && !imgError) {
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
          onError={() => setImgError(true)}
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
        // Muted, brand-adjacent tones — the neon initials broke the
        // "never bright/neon" brand rule on every user list.
        background: `hsl(${hue}, 28%, 40%)`,
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
