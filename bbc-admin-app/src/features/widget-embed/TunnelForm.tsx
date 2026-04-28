import { useState } from 'react'

interface Props {
  tunnel: 'sales' | 'support'
  onSubmit: (data: { name?: string; email?: string; phone?: string; booking_id?: string }) => void
  onBack: () => void
}

export function TunnelForm({ tunnel, onSubmit, onBack }: Props) {
  const [name, setName] = useState('')
  const [phone, setPhone] = useState('')
  const [email, setEmail] = useState('')
  const [bookingId, setBookingId] = useState('')
  const [error, setError] = useState('')

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    if (tunnel === 'sales') {
      if (!name.trim()) return setError('Please enter your name')
      if (!phone.trim()) return setError('Please enter your phone number')
      if (!email.trim() || !email.includes('@')) return setError('Please enter a valid email')
      onSubmit({ name: name.trim(), phone: phone.trim(), email: email.trim() })
    } else {
      if (!email.trim() && !phone.trim()) return setError('Please enter your email or phone')
      if (!bookingId.trim()) return setError('Please enter your ticket number')
      onSubmit({ email: email.trim() || undefined, phone: phone.trim() || undefined, booking_id: bookingId.trim() })
    }
  }

  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '10px 14px', borderRadius: 10,
    border: '1px solid #e5e7eb', fontSize: 14, outline: 'none',
    boxSizing: 'border-box',
  }

  return (
    <div style={{
      position: 'fixed', bottom: 24, right: 24, width: 360,
      background: '#fff', borderRadius: 16,
      boxShadow: '0 8px 30px rgba(0,0,0,0.12)',
      zIndex: 9999, overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{
        background: '#0B1829', color: '#fff', padding: '16px 20px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 15 }}>
            {tunnel === 'sales' ? 'Book Business Class' : 'Support'}
          </div>
          <div style={{ fontSize: 12, opacity: 0.7, marginTop: 2 }}>
            {tunnel === 'sales' ? 'Enter your details to start' : 'We\'ll look into your booking'}
          </div>
        </div>
        <button onClick={onBack} style={{
          background: 'rgba(255,255,255,0.1)', border: 'none', color: '#fff',
          width: 28, height: 28, borderRadius: '50%', cursor: 'pointer', fontSize: 14,
        }}>✕</button>
      </div>

      {/* Form */}
      <form onSubmit={handleSubmit} style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 12 }}>
        {tunnel === 'sales' ? (
          <>
            <input style={inputStyle} placeholder="Your name *" value={name} onChange={e => setName(e.target.value)} />
            <input style={inputStyle} placeholder="Phone number *" type="tel" value={phone} onChange={e => setPhone(e.target.value)} />
            <input style={inputStyle} placeholder="Email address *" type="email" value={email} onChange={e => setEmail(e.target.value)} />
          </>
        ) : (
          <>
            <input style={inputStyle} placeholder="Email or phone *" value={email} onChange={e => setEmail(e.target.value)} />
            <input style={inputStyle} placeholder="Ticket Number *" value={bookingId} onChange={e => setBookingId(e.target.value)} />
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
