import { useState } from 'preact/hooks'
import { COUNTRIES, detectCountryFromPhone, validatePhone, type Country } from './countries'
import brand from './config'

interface Props {
  tunnel: 'sales' | 'support'
  onSubmit: (data: { name?: string; email?: string; phone?: string; country_code?: string; booking_id?: string }) => void
  onBack: () => void
  onInteraction?: () => void
  /** CRM iframe mode: fill the container, hide the close ✕ (panel is always open). */
  embedded?: boolean
}

export function TunnelForm({ tunnel, onSubmit, onBack, onInteraction, embedded = false }: Props) {
  const [name, setName] = useState('')
  const [phone, setPhone] = useState('+')
  const [detectedCountry, setDetectedCountry] = useState<Country>(COUNTRIES[0])
  const [phoneError, setPhoneError] = useState(false)
  const [nameError, setNameError] = useState(false)
  const [email, setEmail] = useState('')
  const [bookingId, setBookingId] = useState('')
  const [error, setError] = useState('')

  const handleSubmit = (e: Event) => {
    e.preventDefault()
    setError('')
    setNameError(false)
    setPhoneError(false)
    if (tunnel === 'sales') {
      const normalizedName = name.trim()
      const nameRegex = /^[A-Za-z' -]+$/
      if (!normalizedName) return setError('Please enter your name')
      const invalidName = normalizedName.length < 2 || normalizedName.length > 20 || /\d/.test(normalizedName)
      if (invalidName || !nameRegex.test(normalizedName)) {
        setNameError(true)
        return setError('Name must be 2-20 letters, no numbers')
      }

      const phoneTrimmed = phone.trim()
      const rawPhone = phoneTrimmed === '+' ? '' : phoneTrimmed
      const digits = rawPhone.replace(/\D/g, '')
      let normalizedPhone = ''
      if (digits.length >= 7) {
        const withCountry = digits.length === 10 ? `1${digits}` : digits
        normalizedPhone = `+${withCountry}`
      }
      if (!normalizedPhone || normalizedPhone.length < 8) {
        setPhoneError(true)
        return setError('Please enter your phone number')
      }
      if (!validatePhone(normalizedPhone, detectedCountry)) {
        setPhoneError(true)
        return setError(`Invalid phone number for ${detectedCountry.name}`)
      }

      if (!email.trim() || !email.includes('@')) return setError('Please enter a valid email')
      onSubmit({
        name: normalizedName,
        email: email.trim(),
        phone: normalizedPhone || undefined,
        country_code: detectedCountry.code,
      })
    } else {
      if (!email.trim() && !phone.trim()) return setError('Please enter your email or phone')
      if (!bookingId.trim()) return setError('Please enter your ticket number')
      onSubmit({ email: email.trim() || undefined, phone: phone.trim() || undefined, country_code: detectedCountry.code, booking_id: bookingId.trim() })
    }
  }

  const inputStyle = {
    width: '100%', padding: '10px 14px', borderRadius: 10,
    border: '1px solid #e5e7eb', fontSize: 14, outline: 'none',
    boxSizing: 'border-box' as const,
  }

  return (
    <div style={embedded ? {
      // CRM iframe mode: the panel IS the page — fill it, no floating-card chrome.
      width: '100%', minHeight: '100%',
      background: '#fff', overflow: 'hidden',
    } : {
      position: 'fixed', bottom: 24, right: 24, width: 360,
      maxWidth: 'calc(100vw - 16px)',
      background: '#fff', borderRadius: 16,
      boxShadow: '0 8px 30px rgba(0,0,0,0.12)',
      zIndex: 2147483000, overflow: 'hidden',
    }}>
      <div style={{
        background: 'var(--bbc-header)', color: 'var(--bbc-header-text)', padding: '16px 20px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <img
            src={brand.logoUrl}
            alt={brand.logoAlt}
            width={28}
            height={28}
            style={{ borderRadius: 6, objectFit: 'contain' }}
          />
          <div>
            <div style={{ fontWeight: 600, fontSize: 15 }}>
              {tunnel === 'sales' ? brand.formTitle : 'Support'}
            </div>
            <div style={{ fontSize: 12, opacity: 0.7, marginTop: 2 }}>
              {tunnel === 'sales' ? brand.formSubtitle : "We'll look into your booking"}
            </div>
          </div>
        </div>
        {!embedded && (
          <button onClick={onBack} aria-label="Close" style={{
            background: 'rgba(255,255,255,0.1)', border: 'none', color: 'var(--bbc-header-text)',
            width: 28, height: 28, borderRadius: '50%', cursor: 'pointer', fontSize: 14,
          }}>✕</button>
        )}
      </div>

      <form onSubmit={handleSubmit} style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
        {tunnel === 'sales' ? (
          <>
            <input style={inputStyle} placeholder="Your name *" value={name} onInput={e => { onInteraction?.(); setName((e.target as HTMLInputElement).value) }} />
            {nameError && (
              <div style={{ fontSize: '11px', color: '#ef4444', marginTop: '-6px' }}>
                Name must be 2-20 letters, no numbers
              </div>
            )}

            <div>
              <label style={{ display: 'block', fontSize: 12, color: '#6b7280', marginBottom: 6 }}>
                Phone <span style={{ color: '#ef4444' }}>*</span>
              </label>
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  background: '#ffffff',
                  border: `1px solid ${phoneError ? '#ef4444' : '#e5e7eb'}`,
                  borderRadius: '10px',
                  overflow: 'hidden',
                  transition: 'border-color 0.2s',
                }}
              >
                <div
                  style={{
                    padding: '10px 12px',
                    borderRight: '1px solid #e5e7eb',
                    fontSize: '18px',
                    lineHeight: 1,
                    minWidth: '48px',
                    textAlign: 'center',
                    userSelect: 'none',
                  }}
                >
                  {detectedCountry.flag}
                </div>

                <input
                  type="tel"
                  value={phone}
                  placeholder="+1 (555) 000-0000"
                  onInput={(e) => {
                    const raw = (e.target as HTMLInputElement).value
                    const trimmed = raw.trim()
                    const val = /^\d/.test(trimmed) ? `+${trimmed}` : (trimmed || '+')
                    setPhone(val)

                    const country = detectCountryFromPhone(val)
                    if (country) {
                      setDetectedCountry(country)
                    } else if (val.startsWith('+')) {
                      // Prevent stale country state (e.g. previously RU) when
                      // the new prefix no longer matches any known dial code.
                      setDetectedCountry(COUNTRIES[0])
                    }

                    const activeCountry = country || (val.startsWith(detectedCountry.dial) ? detectedCountry : null)

                    if (val.length > 1) {
                      const isValid = validatePhone(val, activeCountry)
                      setPhoneError(val.length > 4 && !isValid)
                    } else {
                      setPhoneError(false)
                    }

                    onInteraction?.()
                  }}
                  style={{
                    flex: 1,
                    background: 'transparent',
                    border: 'none',
                    outline: 'none',
                    color: '#111827',
                    fontSize: '14px',
                    padding: '10px 12px',
                    fontFamily: 'inherit',
                  }}
                />
              </div>

              {phone.length > 1 && phone !== '+' && (
                <div style={{ fontSize: '11px', color: '#9ba8b8', marginTop: '4px', paddingLeft: '2px' }}>
                  {detectedCountry.flag} {detectedCountry.name} ({detectedCountry.dial})
                </div>
              )}

              {phoneError && (
                <div style={{ fontSize: '11px', color: '#ef4444', marginTop: '4px', paddingLeft: '2px' }}>
                  Invalid phone number for {detectedCountry.name}
                </div>
              )}
            </div>

            <input style={inputStyle} placeholder="Email address *" type="email" value={email} onInput={e => { onInteraction?.(); setEmail((e.target as HTMLInputElement).value) }} />
          </>
        ) : (
          <>
            <input style={inputStyle} placeholder="Email or phone *" value={email} onInput={e => { onInteraction?.(); setEmail((e.target as HTMLInputElement).value) }} />
            <input style={inputStyle} placeholder="Ticket Number *" value={bookingId} onInput={e => { onInteraction?.(); setBookingId((e.target as HTMLInputElement).value) }} />
          </>
        )}
        {error && <p style={{ color: '#ef4444', fontSize: 12, margin: 0 }}>{error}</p>}
        <button type="submit" style={{
          width: '100%', padding: '12px', borderRadius: 12,
          background: 'var(--bbc-send-btn)', color: 'var(--bbc-send-btn-text)', border: 'none',
          fontWeight: 600, fontSize: 14, cursor: 'pointer',
        }}>
          Start Chat →
        </button>
      </form>
    </div>
  )
}
