import { useEffect, useRef, useState } from 'preact/hooks'
import { COUNTRIES, detectCountryFromPhone, normalizePhone, shownCountry, validatePhone, type Country } from './countries'
import brand from './config'
import { PanelTop } from './PanelTop'
import { IconAlert, IconArrowRight, IconPhone } from './icons'
import { posthog } from './posthog'
import { useIsPhone } from './phoneSheet'

/** PR-12: the trap a person never trips — a hidden field and the fill time.
 *  Sent with the first /chat/start; the server only counts or marks. */
export type FormCheck = { website: string; elapsed_ms: number }

interface Props {
  tunnel: 'sales' | 'support'
  onSubmit: (data: { name?: string; email?: string; phone?: string; country_code?: string; booking_id?: string; form_check?: FormCheck }) => void
  onBack: () => void
  onInteraction?: () => void
  /** CRM iframe mode: fill the container, hide the close ✕ (panel is always open). */
  embedded?: boolean
}

type Field = 'name' | 'phone' | 'email' | 'contact' | 'booking'
type Errors = Partial<Record<Field, string>>

// Letters in any alphabet (José, Ștefan, Zoë), spaces, apostrophes, hyphens and dots; 2 to 40 characters.
// The server does not restrict names; the old 20-letter A-Z rule turned real customers away.
const NAME_RE = /^[\p{L}][\p{L}\p{M}' .-]*$/u

function Err({ id, text }: { id: string; text?: string }) {
  if (!text) return null
  return <div class="bw-error" id={id}><IconAlert />{text}</div>
}

export function TunnelForm({ tunnel, onSubmit, onBack, onInteraction, embedded = false }: Props) {
  const [name, setName] = useState('')
  // Empty, not "+": ten digits typed the US way are a US number, "+" and a code any other country
  // (countries.ts normalizePhone / shownCountry). With "+" already there, "404 555 0143" read as Romania.
  const [phone, setPhone] = useState('')
  const [detectedCountry, setDetectedCountry] = useState<Country>(COUNTRIES[0])
  const [email, setEmail] = useState('')
  const [bookingId, setBookingId] = useState('')
  const [errors, setErrors] = useState<Errors>({})
  const firstRef = useRef<HTMLInputElement>(null)
  const formRef = useRef<HTMLFormElement>(null)
  const isPhone = useIsPhone()
  // PR-12 trap: a hidden field no person fills, and how long the form was open.
  const [website, setWebsite] = useState('')
  const shownAt = useRef(Date.now())
  const formCheck = (): FormCheck => ({ website, elapsed_ms: Date.now() - shownAt.current })

  // Focus the first field on computers; on touch screens a focused field would throw up the keyboard
  // before the visitor has read the panel.
  useEffect(() => {
    try {
      if (window.matchMedia('(pointer: fine)').matches) firstRef.current?.focus({ preventScroll: true })
    } catch { /* matchMedia unavailable — leave focus where it is */ }
  }, [])

  const clear = (f: Field) => setErrors(prev => (prev[f] ? { ...prev, [f]: undefined } : prev))

  const phoneProblem = (value: string): string | undefined => {
    const normalized = normalizePhone(value)
    if (!normalized || normalized.length < 8) return 'Enter your phone number'
    const country = detectCountryFromPhone(normalized) || detectedCountry
    if (!validatePhone(normalized, country)) return `This doesn't look like a ${country.name} number`
    return undefined
  }

  const focusFirstError = (errs: Errors) => {
    const order: Field[] = ['name', 'phone', 'email', 'contact', 'booking']
    const first = order.find(f => errs[f])
    if (!first) return
    formRef.current?.querySelector<HTMLInputElement>(`[data-field="${first}"]`)?.focus()
  }

  const handleSubmit = (e: Event) => {
    e.preventDefault()
    const errs: Errors = {}
    if (tunnel === 'sales') {
      const normalizedName = name.trim().replace(/\s+/g, ' ')
      if (!normalizedName) errs.name = 'Enter your name'
      else if (normalizedName.length < 2 || normalizedName.length > 40 || !NAME_RE.test(normalizedName)) {
        errs.name = 'Use letters only, 2 to 40 characters'
      }
      const pErr = phoneProblem(phone)
      if (pErr) errs.phone = pErr
      if (!email.trim()) errs.email = 'Enter your email'
      else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) errs.email = 'Check your email address'
      setErrors(errs)
      if (Object.values(errs).some(Boolean)) return focusFirstError(errs)
      const normalizedPhone = normalizePhone(phone)
      onSubmit({
        name: normalizedName,
        email: email.trim(),
        phone: normalizedPhone || undefined,
        country_code: (detectCountryFromPhone(normalizedPhone) || detectedCountry).code,
        form_check: formCheck(),
      })
    } else {
      if (!email.trim() && !(phone.trim() && phone.trim() !== '+')) errs.contact = 'Enter your email or phone'
      if (!bookingId.trim()) errs.booking = 'Enter your ticket number'
      setErrors(errs)
      if (Object.values(errs).some(Boolean)) return focusFirstError(errs)
      onSubmit({ email: email.trim() || undefined, phone: phone.trim() || undefined, country_code: detectedCountry.code, booking_id: bookingId.trim(), form_check: formCheck() })
    }
  }

  // The keyboard's "next" key moves to the following field instead of submitting a half-filled form
  // (on a phone it is the same Enter that would otherwise send the form and flash every error).
  const nextOnEnter = (e: KeyboardEvent) => {
    if (e.key !== 'Enter') return
    const fields = Array.from(formRef.current?.querySelectorAll<HTMLInputElement>('input') || [])
    const i = fields.indexOf(e.target as HTMLInputElement)
    if (i > -1 && i < fields.length - 1) {
      e.preventDefault()
      fields[i + 1].focus()
    }
  }

  const heading = tunnel === 'sales' ? brand.formHeading : brand.supportHeading
  const lead = tunnel === 'sales' ? brand.formLead : brand.supportLead
  const phoneShown = /\d/.test(phone)

  return (
    <div
      class={`bw-panel bw-panel--form${embedded ? ' bw-panel--embedded' : ''}`}
      role="dialog"
      aria-modal={isPhone && !embedded ? 'true' : 'false'}
      aria-labelledby="bw-form-title"
    >
      <PanelTop onClose={embedded ? undefined : onBack}>
        <div class="bw-intro">
          <h2 class="bw-intro__title" id="bw-form-title">
            {heading[0]} <em>{heading[1]}</em>
          </h2>
          <p class="bw-intro__lead">{lead}</p>
        </div>
      </PanelTop>

      <form ref={formRef} class="bw-form" onSubmit={handleSubmit} noValidate>
        {/* PR-12 trap: invisible to people and to screen readers, skipped by
            Tab; a form-filling bot finds a "website" field and fills it. */}
        <div aria-hidden="true" style={{ position: 'absolute', left: '-10000px', top: 'auto', width: 1, height: 1, overflow: 'hidden' }}>
          <label>
            Website
            <input type="text" name="website" tabIndex={-1} autoComplete="off" value={website}
              onInput={e => setWebsite((e.target as HTMLInputElement).value)} />
          </label>
        </div>
        {tunnel === 'sales' ? (
          <>
            <div class="bw-field">
              <label class="bw-label" for="bw-name">Your name</label>
              <input
                ref={firstRef}
                id="bw-name"
                onKeyDown={nextOnEnter}
                data-field="name"
                class="bw-input"
                type="text"
                autoComplete="name"
                autoCapitalize="words"
                enterkeyhint="next"
                value={name}
                aria-invalid={errors.name ? 'true' : undefined}
                aria-describedby={errors.name ? 'bw-name-err' : undefined}
                onInput={e => { onInteraction?.(); setName((e.target as HTMLInputElement).value); clear('name') }}
              />
              <Err id="bw-name-err" text={errors.name} />
            </div>

            <div class="bw-field">
              <label class="bw-label" for="bw-phone">Phone number</label>
              <div class="bw-phone" data-invalid={errors.phone ? 'true' : undefined}>
                {phoneShown && (
                  <span class="bw-phone__cc" aria-hidden="true">{detectedCountry.code} {detectedCountry.dial}</span>
                )}
                <input
                  id="bw-phone"
                onKeyDown={nextOnEnter}
                  data-field="phone"
                  class="bw-input"
                  type="tel"
                  inputMode="tel"
                  autoComplete="tel"
                  enterkeyhint="next"
                  value={phone}
                  aria-invalid={errors.phone ? 'true' : undefined}
                  aria-describedby={errors.phone ? 'bw-phone-err' : 'bw-phone-hint'}
                  onInput={(e) => {
                    const val = (e.target as HTMLInputElement).value
                    setPhone(val)
                    // The country the number will be sent with, the same rule as the send (countries.ts).
                    setDetectedCountry(shownCountry(val) || COUNTRIES[0])
                    // A shown error clears as soon as the number becomes valid; a new one waits for blur or submit.
                    if (errors.phone && !phoneProblem(val)) clear('phone')
                    onInteraction?.()
                  }}
                  onBlur={() => { if (phoneShown) setErrors(prev => ({ ...prev, phone: phoneProblem(phone) })) }}
                />
              </div>
              {errors.phone
                ? <Err id="bw-phone-err" text={errors.phone} />
                : <div class="bw-hint" id="bw-phone-hint">{phoneShown ? detectedCountry.name : 'A US number, or + and your country code. For example 404 555 0143'}</div>}
            </div>

            <div class="bw-field">
              <label class="bw-label" for="bw-email">Email address</label>
              <input
                id="bw-email"
                data-field="email"
                class="bw-input"
                type="email"
                inputMode="email"
                autoComplete="email"
                autoCapitalize="off"
                spellcheck={false}
                enterkeyhint="go"
                value={email}
                aria-invalid={errors.email ? 'true' : undefined}
                aria-describedby={errors.email ? 'bw-email-err' : undefined}
                onInput={e => { onInteraction?.(); setEmail((e.target as HTMLInputElement).value); clear('email') }}
              />
              <Err id="bw-email-err" text={errors.email} />
            </div>
          </>
        ) : (
          <>
            <div class="bw-field">
              <label class="bw-label" for="bw-contact">Email or phone</label>
              <input
                ref={firstRef}
                id="bw-contact"
                onKeyDown={nextOnEnter}
                data-field="contact"
                class="bw-input"
                type="text"
                autoComplete="email"
                autoCapitalize="off"
                value={email}
                aria-invalid={errors.contact ? 'true' : undefined}
                aria-describedby={errors.contact ? 'bw-contact-err' : undefined}
                onInput={e => { onInteraction?.(); setEmail((e.target as HTMLInputElement).value); clear('contact') }}
              />
              <Err id="bw-contact-err" text={errors.contact} />
            </div>
            <div class="bw-field">
              <label class="bw-label" for="bw-booking">Ticket number</label>
              <input
                id="bw-booking"
                data-field="booking"
                class="bw-input"
                type="text"
                autoCapitalize="characters"
                spellcheck={false}
                value={bookingId}
                aria-invalid={errors.booking ? 'true' : undefined}
                aria-describedby={errors.booking ? 'bw-booking-err' : undefined}
                onInput={e => { onInteraction?.(); setBookingId((e.target as HTMLInputElement).value); clear('booking') }}
              />
              <Err id="bw-booking-err" text={errors.booking} />
            </div>
          </>
        )}
        <button type="submit" class="bw-cta">
          {brand.ctaSubmit} <IconArrowRight />
        </button>
        {/* For the client who would rather speak to a person than type: the number, big and tappable. */}
        <p class="bw-form__call">
          Prefer to talk?{' '}
          <a
            href={`tel:+${brand.contactPhone.replace(/\D/g, '')}`}
            onClick={() => posthog?.capture('call_initiated', { entry_point: 'widget_form' })}
          >
            <IconPhone /> {brand.contactPhone}
          </a>
        </p>
      </form>
    </div>
  )
}
