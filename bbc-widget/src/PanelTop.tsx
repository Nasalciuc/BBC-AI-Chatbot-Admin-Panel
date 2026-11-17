import type { ComponentChildren } from 'preact'
import { useState } from 'preact/hooks'
import brand from './config'
import { IconClose, IconExpand, IconPhone, IconShrink } from './icons'
import { useLarge } from './largeView'
import { CONSULTANT_LABEL, agentInitials, type Operator } from './operator'
import { PersonaAvatar } from './PersonaAvatar'
import { posthog } from './posthog'

interface Props {
  /** The person who wrote the latest reply (operator.ts, currentOperator): their photo, name and line
   *  take Alex's place, so the client sees who they work with now (owner, 17 Nov 2026). Without it,
   *  Alex. */
  operator?: Operator | null
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
 * readers ("AI Travel Agent · BuyBusinessClass"). The badge that showed the word on his photo is gone
 * (owner, 17 Nov); screen readers still hear it here, in the line.
 */
export function PersonaLine() {
  return <><span class="bw-sr">AI </span>{personaLine}</>
}

/** "Consultant · BuyBusinessClass": the line under the name of a person from the team, the word the
 *  chat already signs their messages with ("Maria, consultant"). */
export const operatorLine = `${CONSULTANT_LABEL} · ${brand.name}`

/**
 * A person's round avatar in the header: their photo, or their initials when there is none or it
 * fails to load; the green dot stays. Decorative, as Alex's is: the name
 * beside it says who it is.
 */
function OperatorAvatar({ operator }: { operator: Operator }) {
  const [broken, setBroken] = useState(false)
  const photo = Boolean(operator.avatarUrl) && !broken
  return (
    <span class={photo ? 'bw-head__ava bw-photo' : 'bw-head__ava'} aria-hidden="true">
      {photo
        ? <img src={operator.avatarUrl as string} alt="" draggable={false} onError={() => setBroken(true)} />
        : agentInitials(operator.name)}
      <i class="bw-head__live" />
    </span>
  )
}

/**
 * The panel's top, as in the BBC Chatbot canvas: Alex's round avatar (his photo, PersonaAvatar.tsx), the name, his line (PersonaLine), a call button, the whole-screen button
 * (largeView.ts) and close. Once a person from the team writes in the chat, their photo, name and
 * line (operatorLine) stand there instead, until the chat comes back to Alex.
 */
export function PanelTop({ operator, onClose, closeLabel = 'Close', canEnlarge = true, children, titleId }: Props) {
  const tel = `tel:+${brand.contactPhone.replace(/\D/g, '')}`
  const [large, setLarge] = useLarge()
  const sizeLabel = large ? 'Make the chat smaller' : 'Make the chat bigger'
  return (
    <div class="bw-top">
      <div class="bw-head">
        {operator
          // Keyed: another person's photo gets its own load (a failed one doesn't stick).
          ? <OperatorAvatar key={`${operator.name}|${operator.avatarUrl || ''}`} operator={operator} />
          : (
            <PersonaAvatar cls="bw-head__ava">
              <i class="bw-head__live" />
            </PersonaAvatar>
          )}
        <div class="bw-head__text">
          <div class="bw-head__name" id={titleId}>{operator ? operator.name : brand.persona.name}</div>
          <div class="bw-head__sub">{operator ? operatorLine : <PersonaLine />}</div>
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
