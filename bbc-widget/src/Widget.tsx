import { useState, useEffect, useRef } from 'preact/hooks'
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

  // Check if a valid session exists in localStorage (survives tab close + browser restart)
  // Used to skip the form when user reopens within 30-minute window
  const hasValidSession = (() => {
    try {
      const convId = localStorage.getItem('bbc_conv_id')
      const ts = localStorage.getItem('bbc_conv_ts')
      if (!convId || !ts) return false
      return Date.now() - parseInt(ts) < SESSION_TTL_MS
    } catch { return false }
  })()

  // Restore tunnel from localStorage if sessionStorage is gone
  const savedTunnel = (() => {
    try {
      return (localStorage.getItem('bbc_conv_tunnel') as 'sales' | 'support') || 'sales'
    } catch { return 'sales' as const }
  })()

  const [step, setStep] = useState<Step>(
    restored?.step === 'chat' || hasValidSession ? 'chat' : 'buttons'
  )
  const [tunnel, setTunnel] = useState<'sales' | 'support'>(
    restored?.tunnel || (hasValidSession ? savedTunnel : 'sales')
  )
  const [visitor, setVisitor] = useState<{ name?: string; email?: string; phone?: string }>(restored?.visitor || {})
  const [metadata, setMetadata] = useState<{ booking_id?: string }>(restored?.metadata || {})

  // Auto-open: after 10 seconds of inactivity, open chat directly for passive visitors.
  // If user enters the form flow and starts typing, auto-open is cancelled and
  // the standard form -> Start Chat procedure applies.
  const autoOpenedRef = useRef(false)
  const userTypingRef = useRef(false)
  const formFlowStartedRef = useRef(false)

  // Track any keyboard activity on the entire page
  useEffect(() => {
    const onKey = () => { userTypingRef.current = true }
    window.addEventListener('keydown', onKey, { capture: true })
    return () => window.removeEventListener('keydown', onKey, { capture: true })
  }, [])

  useEffect(() => {
    if (step !== 'buttons' || autoOpenedRef.current || formFlowStartedRef.current) return
    const timer = setTimeout(() => {
      // If user is typing or already entered the form flow, do not auto-open chat.
      if (userTypingRef.current || formFlowStartedRef.current) return
      autoOpenedRef.current = true
      setTunnel('sales')
      setVisitor({})
      setMetadata({})
      setStep('chat')
      safeSet('bbc_widget', JSON.stringify({ step: 'chat', tunnel: 'sales', visitor: {}, metadata: {} }))
    }, 10_000)
    return () => clearTimeout(timer)
  }, [step])

  const handleTunnelSelect = (t: 'sales' | 'support') => {
    formFlowStartedRef.current = true
    autoOpenedRef.current = true // user intentionally opened widget; cancel auto-open logic
    setTunnel(t)
      const handleFormInteraction = () => {
        // Once user starts filling the form, keep standard manual flow only.
        formFlowStartedRef.current = true
        autoOpenedRef.current = true
      }

    // If valid session exists, skip form and restore chat directly
    if (hasValidSession) {
      setStep('chat')
    } else {
      setStep('form')
    }
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
    // Save tunnel to localStorage so it survives tab close
    try { localStorage.setItem('bbc_conv_tunnel', tunnel) } catch {}
  }

  const handleBack = () => {
    setStep('buttons')
    clearWidgetStorage()
  }

  const handleCloseChat = () => {
    setStep('buttons')
    setVisitor({})
    setMetadata({})
    // Only remove sessionStorage UI state — localStorage persists for 30-minute session
    safeRemove('bbc_widget')
  }

  // Escape key closes form/chat
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && step !== 'buttons') {
        setStep('buttons')
        setVisitor({})
        setMetadata({})
        // Only remove sessionStorage — localStorage persists for 30-minute session
        safeRemove('bbc_widget')
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [step])

  return (
    <>
      {step === 'buttons' && <FloatingButtons onSelect={handleTunnelSelect} />}
      {step === 'form' && (
        <TunnelForm
          tunnel={tunnel}
          onSubmit={handleFormSubmit}
          onBack={handleBack}
          onInteraction={handleFormInteraction}
        />
      )}
      {step === 'chat' && (
        <ChatWindow tunnel={tunnel} visitor={visitor} metadata={metadata} onClose={handleCloseChat} apiUrl={apiUrl} />
      )}
    </>
  )
}
