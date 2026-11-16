import type { ComponentChildren } from 'preact'
import brand from './config'
import { IconClose, IconPhone } from './icons'
import { posthog } from './posthog'

interface Props {
  /** The line under the name; by default "AI travel agent · <brand>". */
  subtitle?: string
  /** Hidden in the CRM iframe, where the panel is always open. */
  onClose?: () => void
  closeLabel?: string
  /** The form's intro (title and one sentence), drawn under the header on the same soft ground. */
  children?: ComponentChildren
  titleId?: string
}

/** "AI travel agent · BuyBusinessClass": who Alex is, said on every screen (ADR 12). */
export const personaLine = `${brand.persona.role} · ${brand.name}`

/**
 * The panel's top, as in the BBC Chatbot canvas: Alex's round avatar (a letter, never an image that
 * can fail to load), the name, the line that says it is an AI travel agent of the brand, a call button
 * and close.
 */
export function PanelTop({ subtitle = personaLine, onClose, closeLabel = 'Close', children, titleId }: Props) {
  const tel = `tel:+${brand.contactPhone.replace(/\D/g, '')}`
  return (
    <div class="bw-top">
      <div class="bw-head">
        <span class="bw-head__ava" aria-hidden="true">
          {brand.persona.initial}
          <i class="bw-head__live" />
        </span>
        <div class="bw-head__text">
          <div class="bw-head__name" id={titleId}>{brand.persona.name}</div>
          <div class="bw-head__sub">{subtitle}</div>
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
