import { useEffect, useRef } from 'preact/hooks'
import brand from './config'
import { IconArrowRight, IconClose } from './icons'

interface Props {
  onSelect: (tunnel: 'sales' | 'support') => void
  /** The greeting beside the button (shown once per visit, after a short wait; Widget.tsx decides). */
  showAttention?: boolean
  onDismissAttention?: () => void
  /** Return keyboard focus here after the panel closes. */
  autoFocus?: boolean
}

/** How long the greeting stays before it steps aside by itself. */
const NUDGE_VISIBLE_MS = 30_000

/**
 * The round button in the corner. Its look (60px navy circle, 2px ring, the logo) must stay exactly as
 * it is: buybusinessclass.com draws a stand-in of it before this script loads and swaps it for this one
 * (FloatingContact.astro); the site also positions #bbc-floating-btn itself.
 */
export function FloatingButtons({ onSelect, showAttention = false, onDismissAttention, autoFocus = false }: Props) {
  const btnRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (autoFocus) btnRef.current?.focus()
  }, [])

  useEffect(() => {
    if (!showAttention || !onDismissAttention) return
    const t = setTimeout(onDismissAttention, NUDGE_VISIBLE_MS)
    return () => clearTimeout(t)
  }, [showAttention])

  return (
    // The round button comes first: the site's hand-over code clicks `#bbc-floating-btn button`.
    <div id="bbc-floating-btn" class="bw-launcher">
      <button
        ref={btnRef}
        type="button"
        class="bw-launch"
        onClick={() => onSelect('sales')}
        aria-label={`Chat with ${brand.name}`}
      >
        <img src={brand.logoUrl} alt="" width={30} height={30} draggable={false} />
      </button>
      {showAttention && (
        <div class="bw-nudge" role="status">
          <button type="button" class="bw-nudge__open" onClick={() => onSelect('sales')}>
            <span class="bw-nudge__q">Questions about a fare?</span>
            <span class="bw-nudge__a">Chat with an expert <IconArrowRight /></span>
          </button>
          <button type="button" class="bw-nudge__x" onClick={onDismissAttention} aria-label="Hide this message">
            <IconClose />
          </button>
        </div>
      )}
    </div>
  )
}
