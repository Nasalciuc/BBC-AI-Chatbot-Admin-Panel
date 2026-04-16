import { useState } from 'preact/hooks'

interface Props {
  tunnel: 'sales' | 'support'
  onSubmit: (data: { name?: string; email?: string; phone?: string; country_code?: string; booking_id?: string }) => void
  onBack: () => void
}

// Popular country codes (ISO 3166-1 alpha-3)
const COUNTRY_CODES = [
  { code: 'US', name: 'United States', dial: '+1' },
  { code: 'GB', name: 'United Kingdom', dial: '+44' },
  { code: 'DE', name: 'Germany', dial: '+49' },
  { code: 'FR', name: 'France', dial: '+33' },
  { code: 'IT', name: 'Italy', dial: '+39' },
  { code: 'ES', name: 'Spain', dial: '+34' },
  { code: 'NL', name: 'Netherlands', dial: '+31' },
  { code: 'BE', name: 'Belgium', dial: '+32' },
  { code: 'CH', name: 'Switzerland', dial: '+41' },
  { code: 'AT', name: 'Austria', dial: '+43' },
  { code: 'CA', name: 'Canada', dial: '+1' },
  { code: 'AU', name: 'Australia', dial: '+61' },
  { code: 'NZ', name: 'New Zealand', dial: '+64' },
  { code: 'SG', name: 'Singapore', dial: '+65' },
  { code: 'HK', name: 'Hong Kong', dial: '+852' },
  { code: 'JP', name: 'Japan', dial: '+81' },
  { code: 'CN', name: 'China', dial: '+86' },
  { code: 'IN', name: 'India', dial: '+91' },
  { code: 'BR', name: 'Brazil', dial: '+55' },
  { code: 'MX', name: 'Mexico', dial: '+52' },
  { code: 'AE', name: 'United Arab Emirates', dial: '+971' },
  { code: 'SA', name: 'Saudi Arabia', dial: '+966' },
  { code: 'ZA', name: 'South Africa', dial: '+27' },
]

export function TunnelForm({ tunnel, onSubmit, onBack }: Props) {
  const [name, setName] = useState('')
  const [countryCode, setCountryCode] = useState('US')
  const [phone, setPhone] = useState('')
  const [email, setEmail] = useState('')
  const [bookingId, setBookingId] = useState('')
  const [error, setError] = useState('')

  const handleSubmit = (e: Event) => {
    e.preventDefault()
    setError('')
    if (tunnel === 'sales') {
      if (!name.trim()) return setError('Please enter your name')
      if (!countryCode) return setError('Please select your country')
      if (!phone.trim()) return setError('Please enter your phone number')
      if (!email.trim() || !email.includes('@')) return setError('Please enter a valid email')
      onSubmit({ name: name.trim(), phone: phone.trim(), country_code: countryCode, email: email.trim() })
    } else {
      if (!email.trim() && !phone.trim()) return setError('Please enter your email or phone')
      if (!bookingId.trim()) return setError('Please enter your booking ID')
      onSubmit({ email: email.trim() || undefined, phone: phone.trim() || undefined, country_code: countryCode || undefined, booking_id: bookingId.trim() })
    }
  }

  const inputStyle = {
    width: '100%', padding: '10px 14px', borderRadius: 10,
    border: '1px solid #e5e7eb', fontSize: 14, outline: 'none',
    boxSizing: 'border-box' as const,
  }

  const selectStyle = {
    ...inputStyle,
    backgroundColor: '#fff',
    cursor: 'pointer',
  }

  return (
    <div style={{
      position: 'fixed', bottom: 24, right: 24, width: 360,
      maxWidth: 'calc(100vw - 16px)',
      background: '#fff', borderRadius: 16,
      boxShadow: '0 8px 30px rgba(0,0,0,0.12)',
      zIndex: 2147483000, overflow: 'hidden',
    }}>
      <div style={{
        background: '#0B1829', color: '#fff', padding: '16px 20px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 15 }}>
            {tunnel === 'sales' ? 'Book Business Class' : 'Support'}
          </div>
          <div style={{ fontSize: 12, opacity: 0.7, marginTop: 2 }}>
            {tunnel === 'sales' ? 'Enter your details to start' : "We'll look into your booking"}
          </div>
        </div>
        <button onClick={onBack} aria-label="Close" style={{
          background: 'rgba(255,255,255,0.1)', border: 'none', color: '#fff',
          width: 28, height: 28, borderRadius: '50%', cursor: 'pointer', fontSize: 14,
        }}>✕</button>
      </div>

      <form onSubmit={handleSubmit} style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
        {tunnel === 'sales' ? (
          <>
            <input style={inputStyle} placeholder="Your name *" value={name} onInput={e => setName((e.target as HTMLInputElement).value)} />
            <div style={{ display: 'flex', gap: 8 }}>
              <select
                style={{ ...selectStyle, flex: '0 0 120px' }}
                value={countryCode}
                onChange={e => setCountryCode((e.target as HTMLSelectElement).value)}
              >
                {COUNTRY_CODES.map(c => (
                  <option key={c.code} value={c.code}>
                    {c.dial} {c.code}
                  </option>
                ))}
              </select>
              <input 
                style={{ ...inputStyle, flex: 1 }}
                placeholder="Phone number *" 
                type="tel" 
                value={phone} 
                onInput={e => setPhone((e.target as HTMLInputElement).value)} 
              />
            </div>
            <input style={inputStyle} placeholder="Email address *" type="email" value={email} onInput={e => setEmail((e.target as HTMLInputElement).value)} />
          </>
        ) : (
          <>
            <input style={inputStyle} placeholder="Email or phone *" value={email} onInput={e => setEmail((e.target as HTMLInputElement).value)} />
            <input style={inputStyle} placeholder="Booking ID *" value={bookingId} onInput={e => setBookingId((e.target as HTMLInputElement).value)} />
          </>
        )}
        {error && <p style={{ color: '#ef4444', fontSize: 12, margin: 0 }}>{error}</p>}
        <button type="submit" style={{
          width: '100%', padding: '12px', borderRadius: 12,
          background: '#C9A54E', color: '#fff', border: 'none',
          fontWeight: 600, fontSize: 14, cursor: 'pointer',
        }}>
          Start Chat →
        </button>
      </form>
    </div>
  )
}
