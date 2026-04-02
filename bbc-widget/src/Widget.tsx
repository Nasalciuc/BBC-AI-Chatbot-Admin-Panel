import { useState, useEffect } from 'preact/hooks'
import { FloatingButtons } from './FloatingButtons'
import { TunnelForm } from './TunnelForm'
import { ChatWindow } from './ChatWindow'

type Step = 'buttons' | 'form' | 'chat'

const SESSION_TTL_MS = 30 * 60 * 1000  // 30 minutes of inactivity

// Safe sessionStorage helpers (storage may be disabled)
function safeGet(key: string): string | null {
  try { return sessionStorage.getItem(key) } catch { return null }
}
function safeSet(key: string, val: string): void {
  try { sessionStorage.setItem(key, val) } catch {}
}
function safeRemove(key: string): void {
  try { sessionStorage.removeItem(key) } catch {}
}

function clearWidgetStorage() {
  safeRemove('bbc_widget')       // sessionStorage
  safeRemove('bbc_conv_id')      // sessionStorage (legacy, may not exist)
  // Also clear localStorage — conv_id lives there
  try { localStorage.removeItem('bbc_conv_id') } catch {}
  try { localStorage.removeItem('bbc_conv_ts') } catch {}
  try { localStorage.removeItem('bbc_visitor_key') } catch {}
}

export function Widget({ apiUrl }: { apiUrl: string }) {
  // Restore from sessionStorage (survives page navigation within same tab)
  const saved = safeGet('bbc_widget')
  const restored = saved ? (() => { try { return JSON.parse(saved) } catch { return null } })() : null

  const [step, setStep] = useState<Step>(restored?.step === 'chat' ? 'chat' : 'buttons')
  const [tunnel, setTunnel] = useState<'sales' | 'support'>(restored?.tunnel || 'sales')
  const [visitor, setVisitor] = useState<{ name?: string; email?: string; phone?: string }>(restored?.visitor || {})
  const [metadata, setMetadata] = useState<{ booking_id?: string }>(restored?.metadata || {})

  const handleTunnelSelect = (t: 'sales' | 'support') => {
    setTunnel(t)
    setStep('form')
  }

  const handleFormSubmit = (data: { name?: string; email?: string; phone?: string; booking_id?: string }) => {
    const vis = { name: data.name, email: data.email, phone: data.phone }
    const meta = data.booking_id ? { booking_id: data.booking_id } : {}

    // Visitor mismatch check — clear old session if different person
    const newKey = `${vis.name || ''}|${vis.email || ''}|${vis.phone || ''}`
    const savedKey = (() => { try { return localStorage.getItem('bbc_visitor_key') } catch { return null } })()
    if (savedKey && newKey && savedKey !== newKey) {
      // Different visitor detected — remove old conversation to prevent mixing
      try { localStorage.removeItem('bbc_conv_id') } catch {}
      try { localStorage.removeItem('bbc_conv_ts') } catch {}
    }
    // Save visitor fingerprint for future mismatch detection
    if (newKey !== '||') {
      try { localStorage.setItem('bbc_visitor_key', newKey) } catch {}
    }

    setVisitor(vis)
    setMetadata(meta)
    setStep('chat')
    safeSet('bbc_widget', JSON.stringify({ step: 'chat', tunnel, visitor: vis, metadata: meta }))
  }

  const handleBack = () => {
    setStep('buttons')
    clearWidgetStorage()
  }

  const handleCloseChat = () => {
    setStep('buttons')
    setVisitor({})
    setMetadata({})
    clearWidgetStorage()
  }

  // Escape key closes form/chat
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && step !== 'buttons') {
        setStep('buttons')
        setVisitor({})
        setMetadata({})
        clearWidgetStorage()
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [step])

  return (
    <>
      {step === 'buttons' && <FloatingButtons onSelect={handleTunnelSelect} />}
      {step === 'form' && <TunnelForm tunnel={tunnel} onSubmit={handleFormSubmit} onBack={handleBack} />}
      {step === 'chat' && (
        <ChatWindow tunnel={tunnel} visitor={visitor} metadata={metadata} onClose={handleCloseChat} apiUrl={apiUrl} />
      )}
    </>
  )
}
