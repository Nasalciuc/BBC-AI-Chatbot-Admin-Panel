import { useEffect, useRef, useState } from 'preact/hooks'
import brand from './config'
import { IconArrowRight, IconClose } from './icons'
import type { PageContext } from './pageContext'
import type { Unread } from './ChatWindow'

interface Props {
  onSelect: (tunnel: 'sales' | 'support') => void
  /** The greeting beside the button (once per visit, after a short wait or on exit intent; Widget.tsx decides). */
  showAttention?: boolean
  onDismissAttention?: () => void
  /** Return keyboard focus here after the panel closes. */
  autoFocus?: boolean
  /** What the page is about: the greeting's words. */
  context?: PageContext
  /** Messages that arrived while the chat was closed: a count on the button and a preview of the latest. */
  unread?: Unread
}

/** How long the greeting stays before it steps aside by itself. */
const NUDGE_VISIBLE_MS = 30_000

/**
 * The round button in the corner. Its look (60px navy circle, 2px ring, the logo) must stay exactly as
 * it is: buybusinessclass.com draws a stand-in of it before this script loads and swaps it for this one
 * (FloatingContact.astro); the site also positions #bbc-floating-btn itself.
 */
export function FloatingButtons({
  onSelect, showAttention = false, onDismissAttention, autoFocus = false, context, unread,
}: Props) {
  const btnRef = useRef<HTMLButtonElement>(null)
  const count = unread?.count || 0
  const preview = unread?.preview
  // The preview of a new message can be put away; a newer message brings it back.
  const [previewHiddenAt, setPreviewHiddenAt] = useState(0)
  const showPreview = count > 0 && !!preview && count !== previewHiddenAt
  // The ring pulses once (twice round) when something appears beside the button, never in a loop.
  const [pulseKey, setPulseKey] = useState(0)

  useEffect(() => {
    if (autoFocus) btnRef.current?.focus()
  }, [])

  useEffect(() => {
    if (!showAttention || !onDismissAttention) return
    setPulseKey(k => k + 1)
    const t = setTimeout(onDismissAttention, NUDGE_VISIBLE_MS)
    return () => clearTimeout(t)
  }, [showAttention])

  useEffect(() => {
    if (unread?.fresh) setPulseKey(k => k + 1)
  }, [unread?.count])

  const label = count > 0
    ? `Chat with ${brand.name}, ${count} new message${count === 1 ? '' : 's'}`
    : `Chat with ${brand.name}`

  return (
    // The round button comes first: the site's hand-over code clicks `#bbc-floating-btn button`.
    <div id="bbc-floating-btn" class="bw-launcher">
      <button
        ref={btnRef}
        type="button"
        class="bw-launch"
        onClick={() => onSelect('sales')}
        aria-label={label}
      >
        <img src={brand.logoUrl} alt="" width={30} height={30} draggable={false} />
        {pulseKey > 0 && <span key={pulseKey} class="bw-launch__pulse" aria-hidden="true" />}
        {count > 0 && <span class="bw-launch__badge" aria-hidden="true">{count > 9 ? '9+' : count}</span>}
      </button>

      {showPreview && preview ? (
        <div class="bw-nudge bw-nudge--message" role="status" aria-live="polite">
          <button type="button" class="bw-nudge__open" onClick={() => onSelect('sales')}>
            <span class="bw-nudge__who">
              {preview.isAgent
                ? (preview.avatar
                    ? <img class="bw-nudge__ava" src={preview.avatar} alt="" width={28} height={28} />
                    : <span class="bw-nudge__ava bw-nudge__ava--initials">{preview.initials}</span>)
                : <img class="bw-nudge__ava bw-nudge__ava--brand" src={brand.logoUrl} alt="" width={28} height={28} />}
              <b>{preview.name}</b>
            </span>
            <span class="bw-nudge__text">{preview.text}</span>
            <span class="bw-nudge__a">Reply <IconArrowRight /></span>
          </button>
          <button type="button" class="bw-nudge__x" onClick={() => setPreviewHiddenAt(count)} aria-label="Hide this message">
            <IconClose />
          </button>
        </div>
      ) : showAttention && count === 0 ? (
        <div class="bw-nudge bw-nudge--message" role="status">
          <button type="button" class="bw-nudge__open" onClick={() => onSelect('sales')}>
            <span class="bw-nudge__who">
              <img class="bw-nudge__ava bw-nudge__ava--brand" src={brand.logoUrl} alt="" width={28} height={28} />
              <b>{brand.name}</b>
            </span>
            <span class="bw-nudge__title">{context?.title || 'Planning a business class trip?'}</span>
            <span class="bw-nudge__text">{context?.line || "Tell me where and when, and I'll check fares for you."}</span>
            <span class="bw-nudge__cta">Get a fare quote <IconArrowRight /></span>
          </button>
          <button type="button" class="bw-nudge__x" onClick={onDismissAttention} aria-label="Hide this message">
            <IconClose />
          </button>
        </div>
      ) : null}
    </div>
  )
}
