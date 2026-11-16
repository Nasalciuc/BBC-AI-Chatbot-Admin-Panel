import type { ComponentChildren } from 'preact'
import brand from './config'
import { IconClose, IconExpand, IconPhone, IconShrink } from './icons'
import { useLarge } from './largeView'
import { PersonaAvatar } from './PersonaAvatar'
import { posthog } from './posthog'

interface Props {
  /** The line under the name when a person took the chat ("Chatting with Maria"); without it, Alex's
   *  own line (PersonaLine). */
  subtitle?: string
  /** Hidden in the CRM iframe, where the panel is always open. */
  onClose?: () => void
  closeLabel?: string
  /** The button that opens the chat over the whole screen (computers). Not in the CRM iframe. */
  canEnlarge?: boolean
  /** The form's intro (title and one sentence), drawn under the header on the same soft ground. */
  children?: ComponentChildren
  titleId?: string
}

/** "Travel Agent · BuyBusinessClass": who Alex is, on every screen that shows him (ADR 12; owner, 16 Nov). */
export const personaLine = `${brand.persona.role} · ${brand.name}`

/**
 * Alex's line as the page shows it: "Travel Agent · BuyBusinessClass", with "AI" before it for screen
 * readers ("AI Travel Agent · BuyBusinessClass"). Seen, the word is the badge on his photo
 * (PersonaAvatar.tsx), a picture that screen readers skip, so they get it here, in the same place.
 */
export function PersonaLine() {
  return <><span class="bw-sr">AI </span>{personaLine}</>
}

/**
 * The panel's top, as in the BBC Chatbot canvas: Alex's round avatar (his photo with the "AI" badge,
 * PersonaAvatar.tsx), the name, his line (PersonaLine), a call button, the whole-screen button
 * (largeView.ts) and close.
 */
export function PanelTop({ subtitle, onClose, closeLabel = 'Close', canEnlarge = true, children, titleId }: Props) {
  const tel = `tel:+${brand.contactPhone.replace(/\D/g, '')}`
  const [large, setLarge] = useLarge()
  const sizeLabel = large ? 'Make the chat smaller' : 'Make the chat bigger'
  return (
    <div class="bw-top">
      <div class="bw-head">
        <PersonaAvatar cls="bw-head__ava">
          <i class="bw-head__live" />
        </PersonaAvatar>
        <div class="bw-head__text">
          <div class="bw-head__name" id={titleId}>{brand.persona.name}</div>
          <div class="bw-head__sub">{subtitle || <PersonaLine />}</div>
        </div>
        <div class="bw-head__actions">
          <a
            class="bw-iconbtn bw-iconbtn--call"
            href={tel}
            aria-label={`Call ${brand.name} at ${brand.contactPhone}`}
            title={`Call ${brand.contactPhone}`}
            onClick={() => posthog?.capture('call_initiated', { entry_point: 'widget_header' })}
          >
            <IconPhone />
          </a>
          {canEnlarge && (
            // The label says what a press does (no aria-pressed: a label that changes with the state
            // and a pressed state together read twice to a screen reader).
            <button
              type="button"
              class="bw-iconbtn bw-iconbtn--size"
              aria-label={sizeLabel}
              title={sizeLabel}
              onClick={() => {
                setLarge(!large)
                posthog?.capture('widget_large_view', { on: !large })
              }}
            >
              {large ? <IconShrink /> : <IconExpand />}
            </button>
          )}
          {onClose && (
            <button type="button" class="bw-iconbtn" onClick={onClose} aria-label={closeLabel} title={closeLabel}>
              <IconClose />
            </button>
          )}
        </div>
      </div>
      {children}
    </div>
  )
}
