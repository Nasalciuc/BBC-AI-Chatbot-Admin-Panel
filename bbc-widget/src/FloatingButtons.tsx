import { useEffect, useRef, useState } from 'preact/hooks'
import type { ChannelKind } from './channel'
import brand from './config'
import { words, widgetLang } from './i18n'
import { IconArrowRight, IconClose, IconMail, IconMessage } from './icons'
import { PersonaLine } from './PanelTop'
import { PersonaAvatar } from './PersonaAvatar'
import type { PageContext } from './pageContext'
import type { Unread } from './ChatWindow'
import { markNoteClosed, type ProactiveOpening } from './proactive'
import { isNotNow } from './triggers'

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
  /** The proactive card (phones): the server's words for this visitor's search and two buttons. */
  card?: ProactiveOpening | null
  /** Open the chat from the card; `reply` is the button tapped, sent as the visitor's first message. */
  onCardOpen?: (reply?: string) => void
  /** "Not now" on the card. */
  onCardDismiss?: () => void
  /** A tap on the 10 s note: the chat with Alex and no form (Widget.tsx). Without it, the form. */
  onNoteOpen?: () => void
  /** C1: the card's own channel button (WhatsApp on a phone, email on a computer): the chat opens on its
   *  field, no message sent before the client leaves the contact. */
  onCardChannel?: (kind: ChannelKind) => void
  /** C2: the pill after "Not now" — the card's channel, small, until ✕ or a tap. */
  pill?: ProactiveOpening | null
  onPillOpen?: () => void
  onPillClose?: () => void
}

/** The icon of a channel: a message bubble for WhatsApp, an envelope for email (never a brand's logo). */
export function ChannelIcon({ kind }: { kind: ChannelKind }) {
  return kind === 'whatsapp' ? <IconMessage size={18} /> : <IconMail size={18} />
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
  card, onCardOpen, onCardDismiss, onNoteOpen, onCardChannel, pill, onPillOpen, onPillClose,
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

  useEffect(() => {
    if (card) setPulseKey(k => k + 1)
  }, [card])

  // The card: its first button is the server's first reply ("Yes, send me fares"); "Not now" closes it.
  const cardYes = card?.quickReplies.find(r => !isNotNow(r))
  const cardNo = card?.quickReplies.find(isNotNow) || 'Not now'
  // C1: the card's own channel, between the yes and "Not now".
  const cardChannel = card?.channel && onCardChannel ? card.channel : null

  const label = count > 0
    ? `Chat with ${brand.name}, ${count} new message${count === 1 ? '' : 's'}`
    : `Chat with ${brand.name}`
  // Alex's avatar beside the button: his photo (PersonaAvatar.tsx).
  const personaAvatar = <PersonaAvatar cls="bw-nudge__ava bw-nudge__ava--persona" />
  const personaWho = (
    <span class="bw-nudge__id"><b>{brand.persona.name}</b><small><PersonaLine /></small></span>
  )

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
                : personaAvatar}
              <b>{preview.name}</b>
            </span>
            <span class="bw-nudge__text">{preview.text}</span>
            <span class="bw-nudge__a">Reply <IconArrowRight /></span>
          </button>
          <button type="button" class="bw-nudge__x" onClick={() => setPreviewHiddenAt(count)} aria-label="Hide this message">
            <IconClose />
          </button>
        </div>
      ) : card && count === 0 ? (
        <div class="bw-nudge bw-nudge--card" role="status" aria-live="polite">
          <button type="button" class="bw-nudge__open" onClick={() => onCardOpen?.()}>
            <span class="bw-nudge__who">{personaAvatar}{personaWho}</span>
            {card.headline && card.cardText ? (
              // ADR A3: the trip first, in one line a visitor of three seconds reads, then only the question.
              <>
                <span class="bw-nudge__headline">{card.headline}</span>
                <span class="bw-nudge__text">{card.cardText}</span>
              </>
            ) : (
              <span class="bw-nudge__text">{card.message}</span>
            )}
          </button>
          <div class={`bw-card__actions${cardChannel ? ' bw-card__actions--channel' : ''}`}>
            {cardYes && (
              <button type="button" class="bw-card__yes" onClick={() => onCardOpen?.(cardYes)}>
                {cardYes}
              </button>
            )}
            {cardChannel && (
              <button type="button" class="bw-card__channel" onClick={() => onCardChannel?.(cardChannel.kind)}>
                <ChannelIcon kind={cardChannel.kind} />{cardChannel.label}
              </button>
            )}
            <button type="button" class="bw-card__no" onClick={onCardDismiss}>{cardNo}</button>
          </div>
        </div>
      ) : pill?.channel && count === 0 ? (
        <div class="bw-pill" role="status">
          <button type="button" class="bw-pill__open" onClick={onPillOpen}>
            <ChannelIcon kind={pill.channel.kind} />{pill.channel.label}
          </button>
          <button type="button" class="bw-pill__x" onClick={onPillClose} aria-label={words(widgetLang()).hide}>
            <IconClose />
          </button>
        </div>
      ) : showAttention && count === 0 ? (
        <div class="bw-nudge bw-nudge--message" role="status">
          <button type="button" class="bw-nudge__open" onClick={() => (onNoteOpen ? onNoteOpen() : onSelect('sales'))}>
            <span class="bw-nudge__who">{personaAvatar}{personaWho}</span>
            <span class="bw-nudge__title">{context?.title || 'Planning a business class trip?'}</span>
            <span class="bw-nudge__text">{context?.line || 'Tell me where and when, and an agent will check fares for you.'}</span>
            <span class="bw-nudge__cta">Get a fare quote <IconArrowRight /></span>
          </button>
          <button
            type="button"
            class="bw-nudge__x"
            onClick={() => { markNoteClosed(); onDismissAttention?.() }}
            aria-label="Hide this message"
          >
            <IconClose />
          </button>
        </div>
      ) : null}
    </div>
  )
}
