import { render } from 'preact'
import type { ChannelKind } from './channel'
import brand from './config'
import { ChannelIcon } from './FloatingButtons'
import { words, widgetLang } from './i18n'
import { IconClose } from './icons'
import type { ProactiveOpening } from './proactive'

/**
 * D1 (ADR-4a, 18 Nov 2026): a row beside the site's quote form after it refused a send, in place of the
 * chat card ("Looks like the quote form is giving you trouble" caught 0 of 23). 64 sends were refused in a
 * day and 54 of those people never sent anything after: they pressed "Get free quote" to see a price, and
 * the form asked for three personal details first. The row offers a shortcut, never a reproach: the fares
 * by SMS or by email, one field — the chat opens on it (ChatWindow, `firstChannel`).
 *
 * It sits under the form's button, inside the form's card and outside the widget's root, so it has its own
 * styles (styles.ts, #bbc-form-row) and the brand's colour set on its host. It covers nothing and does not
 * fight the site's own error messages, which stay beside their fields above it. Its buttons are
 * type="button": nothing in it ever sends the site's form.
 */
export function FormRow({ opening, onPick, onClose }: {
  opening: ProactiveOpening
  onPick: (kind: ChannelKind) => void
  onClose: () => void
}) {
  const hide = words(widgetLang()).hide
  return (
    <div class="bw-row-offer" role="region" aria-label={opening.message}>
      <p class="bw-row-offer__text">{opening.message}</p>
      <div class="bw-row-offer__actions">
        {(opening.buttons || []).filter(b => b.kind === 'sms' || b.kind === 'email').map(b => (
          <button key={b.kind} type="button" class="bw-row-offer__btn" onClick={() => onPick(b.kind as ChannelKind)}>
            <ChannelIcon kind={b.kind as ChannelKind} />{b.label}
          </button>
        ))}
      </div>
      <button type="button" class="bw-row-offer__x" onClick={onClose} aria-label={hide}><IconClose size={16} /></button>
    </div>
  )
}

/** Where the row stands: right under the button the visitor just pressed, inside the form's own card (beside
 *  the form, a page lays it out in its own grid: the row could land in another column, on another
 *  background); at the end of the form when the form has no button we know. */
function rowPlace(form: HTMLFormElement): { at: Element; where: InsertPosition } {
  const button = form.querySelector('button[type="submit"], input[type="submit"], button:not([type])')
  return button ? { at: button, where: 'afterend' } : { at: form, where: 'beforeend' }
}

/** Puts the row under the form's button and returns the way to take it away. */
export function placeFormRow(
  form: HTMLFormElement, opening: ProactiveOpening, onPick: (kind: ChannelKind) => void, onClose: () => void,
): () => void {
  const host = document.createElement('div')
  host.id = 'bbc-form-row'
  host.style.setProperty('--bw-primary', brand.look.primary)
  host.style.setProperty('--bw-primary-rgb', brand.look.primaryRgb)
  host.style.setProperty('--bw-primary-hover', brand.look.primaryHover)
  const { at, where } = rowPlace(form)
  if (where === 'afterend') {
    // As wide as the button above it, whatever the site's layout gives the form's children.
    try {
      const cs = window.getComputedStyle(at)
      host.style.marginLeft = cs.marginLeft
      host.style.marginRight = cs.marginRight
    } catch { /* no styles to read: the row takes the form's width */ }
  }
  at.insertAdjacentElement(where, host)
  render(<FormRow opening={opening} onPick={onPick} onClose={onClose} />, host)
  return () => {
    render(null, host)
    host.remove()
  }
}
