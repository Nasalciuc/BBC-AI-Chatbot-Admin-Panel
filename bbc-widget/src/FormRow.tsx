import { render } from 'preact'
import { useState } from 'preact/hooks'
import { guessCountry, readRowContact, rowShowsCountry, type ChannelKind } from './channel'
import brand from './config'
import { COUNTRIES, type Country } from './countries'
import { browserLanguage, timeZone, widgetLang, words } from './i18n'
import { IconClose } from './icons'
import type { ProactiveOpening } from './proactive'
import { ROW_CSS } from './styles'

/**
 * D1 (ADR-4a, 18 Nov 2026): a row beside the site's quote form after it refused a send, in place of the
 * chat card ("Looks like the quote form is giving you trouble" caught 0 of 23). 64 sends were refused in a
 * day and 54 of those people never sent anything after: they pressed "Get free quote" to see a price, and
 * the form asked for three personal details first. The row offers a shortcut, never a reproach.
 *
 * Since 23 Nov (owner) the row takes the contact itself: one field, an email or a mobile number, and "Send me
 * fares". It comes at once after the refusal (triggers.ts QUOTE_ROW_IDLE_S): about 73 visitors a day were refused
 * without a whole contact and 71% of them left within 5 s, while the row came after 2 s and asked for a tap on a
 * channel first. A visitor who gave the form one contact and was refused the other is told one is enough
 * (channel.ts rowField). What they leave goes out as their first message in the chat, which opens on it.
 *
 * It sits under the form's button, inside the form's card and outside the widget's root, in a tree of its own (a
 * shadow root): its field belongs to no form of the site, so the site's form never checks, sends or reads it, and
 * Enter in it never sends the site's form; the site's styles stay out (styles.ts ROW_CSS) and the brand's colour
 * comes from its host. It covers nothing and does not fight the site's own error messages, which stay beside
 * their fields above it.
 */
export function FormRow({ opening, onSend, onClose }: {
  opening: ProactiveOpening
  onSend: (kind: ChannelKind, contact: string) => void
  onClose: () => void
}) {
  const w = words(widgetLang())
  const field = opening.rowField ?? 'any'
  const [value, setValue] = useState('')
  const [error, setError] = useState('')
  // The country a number typed without its code goes to: the browser's guess, shown beside the field and changeable,
  // as in the chat's own field (CodeRabbit #279: an en-GB browser placed a US number in the UK, unseen).
  const [country, setCountry] = useState<Country>(() => guessCountry(COUNTRIES, browserLanguage(), timeZone()))
  const one = field === 'email' ? w.rowOneEmail : field === 'sms' ? w.rowOneSms : ''
  const label = field === 'email' ? w.emailLabel : field === 'sms' ? w.phoneLabel : w.rowAny
  const showCountry = rowShowsCountry(field, value)
  const send = () => {
    const read = readRowContact(value, COUNTRIES, country)
    if ('error' in read) {
      setError(read.error === 'email' ? w.badEmail : read.error === 'phone' ? w.badPhone : w.badEither)
      return
    }
    onSend(read.kind, read.contact)
  }
  return (
    <div class="bw-row-offer" role="region" aria-label={opening.message}>
      <p class="bw-row-offer__text">{opening.message}</p>
      {one && <p class="bw-row-offer__one">{one}</p>}
      <div class="bw-row-offer__form">
        {showCountry && (
          <select
            class="bw-row-offer__cc"
            aria-label={w.country}
            value={country.code}
            onChange={(e) => {
              const picked = COUNTRIES.find(c => c.code === (e.target as HTMLSelectElement).value)
              if (picked) setCountry(picked)
              if (error) setError('')
            }}
          >
            {COUNTRIES.map(c => <option key={c.code} value={c.code}>{`${c.flag} ${c.dial}`}</option>)}
          </select>
        )}
        <input
          class="bw-row-offer__input"
          // Text, not email: an email or a number go in the same field, and a text field is never "invalid".
          type="text"
          inputMode={field === 'sms' ? 'tel' : field === 'email' ? 'email' : 'text'}
          autoComplete={field === 'sms' ? 'tel' : 'email'}
          autoCapitalize="off"
          spellcheck={false}
          placeholder={label}
          aria-label={label}
          aria-invalid={error ? 'true' : undefined}
          aria-describedby={error ? 'bw-row-error' : undefined}
          value={value}
          onInput={(e) => { setValue((e.target as HTMLInputElement).value); if (error) setError('') }}
          onKeyDown={(e) => {
            if (e.key !== 'Enter') return
            e.preventDefault()
            send()
          }}
        />
        <button type="button" class="bw-row-offer__btn" onClick={send}>{w.rowSend}</button>
      </div>
      {error && <p class="bw-row-offer__error" id="bw-row-error" role="alert">{error}</p>}
      <button type="button" class="bw-row-offer__x" onClick={onClose} aria-label={w.hide}><IconClose size={16} /></button>
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

// What the visitor does in the row stays in the row: the site's own form scripts never hear its keys or its typing.
const KEPT_IN_ROW = ['keydown', 'keypress', 'keyup', 'input', 'change', 'beforeinput', 'focusin', 'focusout'] as const
const keep = (e: Event) => e.stopPropagation()

/** Puts the row under the form's button and returns the way to take it away. */
export function placeFormRow(
  form: HTMLFormElement, opening: ProactiveOpening, onSend: (kind: ChannelKind, contact: string) => void,
  onClose: () => void,
): () => void {
  const host = document.createElement('div')
  // Every browser the sites see has shadow roots (Safari 10, Chrome 53, Firefox 63). One without them gets no row:
  // its field would belong to the site's form, which would then check it and send it with its own.
  if (typeof host.attachShadow !== 'function') return () => {}
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
  for (const type of KEPT_IN_ROW) host.addEventListener(type, keep)
  const root = host.attachShadow({ mode: 'open' })
  render(
    <>
      <style>{ROW_CSS}</style>
      <FormRow opening={opening} onSend={onSend} onClose={onClose} />
    </>,
    root,
  )
  return () => {
    render(null, root)
    for (const type of KEPT_IN_ROW) host.removeEventListener(type, keep)
    host.remove()
  }
}
