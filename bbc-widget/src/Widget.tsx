import { useState, useEffect, useRef } from 'preact/hooks'
import { FloatingButtons } from './FloatingButtons'
import { TunnelForm } from './TunnelForm'
import { ChatWindow } from './ChatWindow'
import { getVisitorId } from './api'
import { getUtm } from './utm'

type Step = 'buttons' | 'form' | 'chat'

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

/** Generate a visitor_id (crypto.randomUUID with fallback). */
function makeVisitorId(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
  // Fallback for older browsers — not cryptographically strong but unique enough
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const r = (Math.random() * 16) | 0
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16)
  })
}

/** Ensure a visitor_id exists in localStorage, creating one if needed. */
function ensureVisitorId(): string {
  const existing = getVisitorId()
  if (existing) return existing
  const id = makeVisitorId()
  try { localStorage.setItem('bbc_visitor_id', id) } catch {}
  return id
}

function clearWidgetStorage() {
  safeRemove('bbc_widget')       // sessionStorage
  // Clear localStorage session cache (but NOT bbc_visitor_id — that persists forever)
  try { localStorage.removeItem('bbc_conv_id') } catch {}
  try { localStorage.removeItem('bbc_conv_ts') } catch {}
  try { localStorage.removeItem('bbc_visitor_key') } catch {}
  try { localStorage.removeItem('bbc_conv_booking_id') } catch {}
}

export function Widget({ apiUrl }: { apiUrl: string }) {
  // Restore from sessionStorage (survives page navigation within same tab)
  const saved = safeGet('bbc_widget')
  const restored = saved ? (() => { try { return JSON.parse(saved) } catch { return null } })() : null

  // ── Corrupt value repair ─────────────────────────────────────────────────
  // An earlier backend bug (UnboundLocalError in orchestrator.py) caused the
  // chat endpoint to return conversation_id: "unknown" as a literal string.
  // Widgets from that period persisted it in localStorage and then sent it
  // back on every subsequent request. Clear on mount so they self-heal.
  // Safe to remove 30 days after 2026-04-20.
  try {
    if (localStorage.getItem('bbc_conv_id') === 'unknown') {
      localStorage.removeItem('bbc_conv_id')
      localStorage.removeItem('bbc_conv_ts')
      localStorage.removeItem('bbc_conv_tunnel')
      localStorage.removeItem('bbc_conv_booking_id')
      // Intentionally do NOT clear bbc_visitor_id — that UUID is fine;
      // only the conversation pointer was corrupt.
    }
  } catch { /* localStorage disabled — no corrupt value to clear */ }

  // ── Optimistic session check ──────────────────────────────────────────────
  // Check localStorage for cached conv_id. This is a local cache only — the
  // backend verify (below) is the authoritative source. If the backend says
  // the conversation is gone, we clear this cache and show buttons.
  const cachedConvId = (() => {
    try { return localStorage.getItem('bbc_conv_id') } catch { return null }
  })()

  // A visitor_id in localStorage means we had a session before (possibly
  // across a browser restart). Combined with cachedConvId, we can optimistically
  // show the chat UI immediately and verify with backend in parallel.
  const hasOptimisticSession = !!(getVisitorId() && cachedConvId)

  // Restore tunnel from localStorage if sessionStorage is gone
  const savedTunnel = (() => {
    try {
      return (localStorage.getItem('bbc_conv_tunnel') as 'sales' | 'support') || 'sales'
    } catch { return 'sales' as const }
  })()

  // Restore visitor identity from localStorage when sessionStorage has died.
  // `bbc_visitor_key` stores "name|email|phone" for display purposes.
  const savedVisitor = (() => {
    if (!hasOptimisticSession) return null
    try {
      const key = localStorage.getItem('bbc_visitor_key')
      if (!key || key === '||') return null
      const parts = key.split('|')
      if (parts.length !== 3) return null
      const [name, email, phone] = parts
      if (!name && !email && !phone) return null
      return {
        name:  name  || undefined,
        email: email || undefined,
        phone: phone || undefined,
      }
    } catch { return null }
  })()

  // Restore booking_id from localStorage too
  const savedMetadata = (() => {
    if (!hasOptimisticSession) return null
    try {
      const bookingId = localStorage.getItem('bbc_conv_booking_id')
      return {
        ...getUtm(),
        ...(bookingId ? { booking_id: bookingId } : {}),
      }
    } catch { return null }
  })()

  const [step, setStep] = useState<Step>(
    restored?.step === 'chat' || (hasOptimisticSession && savedVisitor) ? 'chat' : 'buttons'
  )
  const [tunnel, setTunnel] = useState<'sales' | 'support'>(
    restored?.tunnel || (hasOptimisticSession ? savedTunnel : 'sales')
  )
  const [visitor, setVisitor] = useState<{ name?: string; email?: string; phone?: string; country_code?: string }>(
    restored?.visitor || savedVisitor || {}
  )
  const [metadata, setMetadata] = useState<{ booking_id?: string }>(
    restored?.metadata || savedMetadata || {}
  )
  const [showAttention, setShowAttention] = useState(false)

  // Auto-open refs
  const autoOpenedRef = useRef(false)
  const formFlowStartedRef = useRef(false)

  // Intent detection refs
  const intentFiredRef = useRef(false)
  const intentDwellTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const intentChatTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const cachedBtnRect = useRef<DOMRect | null>(null)

  // ── Backend verify on mount ───────────────────────────────────────────────
  // If we optimistically jumped to 'chat', ask the backend if the conversation
  // is still active. If not, fall back to buttons.
  useEffect(() => {
    if (!hasOptimisticSession) return
    const visitorId = getVisitorId()
    if (!visitorId) return

    let cancelled = false
    fetch(`${apiUrl}/api/chat/visitor/${encodeURIComponent(visitorId)}/active-conversation`)
      .then(r => r.json())
      .then(data => {
        if (cancelled) return
        if (data.success && data.conversation_id) {
          // Backend confirmed — update cached conv_id (may have changed)
          try { localStorage.setItem('bbc_conv_id', data.conversation_id) } catch {}
        } else {
          // No active conversation — clear cache and fall back
          try { localStorage.removeItem('bbc_conv_id') } catch {}
          try { localStorage.removeItem('bbc_conv_ts') } catch {}
          // Only fall back if user hasn't navigated away from chat already
          setStep(prev => prev === 'chat' ? 'buttons' : prev)
        }
      })
      .catch(() => {
        // Network error — keep optimistic UI (don't disrupt active session)
      })
    return () => { cancelled = true }
  // eslint-disable-next-line
  }, [])

  // Injectăm @keyframes în <head> — o singură dată la mount
  // Inline style nu suportă @keyframes → trebuie <style> tag
  useEffect(() => {
    const styleId = 'bbc-widget-keyframes'
    if (document.getElementById(styleId)) return // deja injectat

    const style = document.createElement('style')
    style.id = styleId
    style.textContent = `
      @keyframes bbc-bounce {
        0%, 100% { transform: translateY(0) scale(1); }
        30%       { transform: translateY(-10px) scale(1.08); }
        60%       { transform: translateY(-5px) scale(1.03); }
      }
      @keyframes bbc-fadein {
        from { opacity: 0; transform: translateY(6px); }
        to   { opacity: 1; transform: translateY(0); }
      }
    `
    document.head.appendChild(style)
    // Nu facem cleanup — keyframes rămân pe tot parcursul sesiunii
  }, [])

  // ─── EFFECT A: Auto-open form (10 secunde inactivitate) ─────────────────────
  useEffect(() => {
    if (step !== 'buttons') return
    if (autoOpenedRef.current || formFlowStartedRef.current) return
    if (safeGet('bbc_attention_shown') === '1') return

    const attentionTimer = setTimeout(() => {
      if (formFlowStartedRef.current) return
      // Deschide formularul direct, nu doar tooltip
      autoOpenedRef.current = true
      formFlowStartedRef.current = true
      setShowAttention(false)
      setTunnel('sales')
      setStep('form')
      safeSet('bbc_attention_shown', '1')
    }, 10_000)

    return () => clearTimeout(attentionTimer)
  }, [step])

  // ─── EFFECT B: Intent Detection — Desktop (mouse dwell lângă buton) ──────────
  useEffect(() => {
    if (step !== 'buttons') return

    // Mobile detection: touch device fără mouse precis
    const isMobile = navigator.maxTouchPoints > 0 &&
      !window.matchMedia('(pointer: fine)').matches
    if (isMobile) return

    // Cache rect-ul butonului (recalculat la resize)
    const updateRect = () => {
      const el = document.getElementById('bbc-floating-btn')
      if (el) cachedBtnRect.current = el.getBoundingClientRect()
    }
    updateRect()
    window.addEventListener('resize', updateRect, { passive: true })

    const handleMouseMove = (e: MouseEvent) => {
      if (intentFiredRef.current || formFlowStartedRef.current) return

      const rect = cachedBtnRect.current
      if (!rect) return

      const dist = Math.hypot(
        e.clientX - (rect.left + rect.width / 2),
        e.clientY - (rect.top + rect.height / 2)
      )

      if (dist < 180) {
        if (!intentDwellTimer.current) {
          intentDwellTimer.current = setTimeout(() => {
            if (formFlowStartedRef.current) return
            intentFiredRef.current = true

            intentChatTimer.current = setTimeout(() => {
              if (formFlowStartedRef.current) return
              autoOpenedRef.current = true
              setTunnel('sales')
              setStep('form')
            }, 10_000)
          }, 1_500)
        }
      }
    }

    window.addEventListener('mousemove', handleMouseMove, { passive: true })

    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('resize', updateRect)
      if (intentDwellTimer.current) clearTimeout(intentDwellTimer.current)
      if (intentChatTimer.current) clearTimeout(intentChatTimer.current)
    }
  }, [step])

  // ─── EFFECT C: Mobile Fallback (3 minute timer simplu) ───────────────────────
  useEffect(() => {
    const isMobile = navigator.maxTouchPoints > 0 &&
      !window.matchMedia('(pointer: fine)').matches
    if (!isMobile || step !== 'buttons') return
    if (autoOpenedRef.current || formFlowStartedRef.current) return

    const mobileTimer = setTimeout(() => {
      if (formFlowStartedRef.current) return
      autoOpenedRef.current = true
      setTunnel('sales')
      setStep('form')
    }, 3 * 60 * 1000) // 3 minutes

    return () => clearTimeout(mobileTimer)
  }, [step])

  const handleTunnelSelect = (t: 'sales' | 'support') => {
    setShowAttention(false)
    formFlowStartedRef.current = true
    autoOpenedRef.current = true
    setTunnel(t)

    // Skip form ONLY if visitor completed form before (has name+email+phone)
    if (hasOptimisticSession && savedVisitor) {
      setStep('chat')
    } else {
      setStep('form')
    }
  }

  const handleFormInteraction = () => {
    formFlowStartedRef.current = true
    autoOpenedRef.current = true
  }

  const handleFormSubmit = (data: { name?: string; email?: string; phone?: string; country_code?: string; booking_id?: string }) => {
    // Ensure visitor_id exists before entering chat
    ensureVisitorId()

    const vis = { name: data.name, email: data.email, phone: data.phone, country_code: data.country_code }
    const meta = {
      ...getUtm(),
      ...(data.booking_id ? { booking_id: data.booking_id } : {}),
    }

    // Visitor mismatch check — clear old session if different person
    const newKey = `${vis.name || ''}|${vis.email || ''}|${vis.phone || ''}`
    const savedKey = (() => { try { return localStorage.getItem('bbc_visitor_key') } catch { return null } })()
    if (savedKey && newKey && savedKey !== newKey) {
      // Different visitor — new visitor_id + clear old conversation cache
      const newId = makeVisitorId()
      try { localStorage.setItem('bbc_visitor_id', newId) } catch {}
      try { localStorage.removeItem('bbc_conv_id') } catch {}
      try { localStorage.removeItem('bbc_conv_ts') } catch {}
    }
    // Save visitor fingerprint for display
    if (newKey !== '||') {
      try { localStorage.setItem('bbc_visitor_key', newKey) } catch {}
    }

    setVisitor(vis)
    setMetadata(meta)
    setStep('chat')
    safeSet('bbc_widget', JSON.stringify({ step: 'chat', tunnel, visitor: vis, metadata: meta }))
    try { localStorage.setItem('bbc_conv_tunnel', tunnel) } catch {}
    if (data.booking_id) {
      try { localStorage.setItem('bbc_conv_booking_id', data.booking_id) } catch {}
    } else {
      try { localStorage.removeItem('bbc_conv_booking_id') } catch {}
    }
  }

  const handleBack = () => {
    setStep('buttons')
    clearWidgetStorage()
  }

  const handleCloseChat = () => {
    setStep('buttons')
    // Keep localStorage intact (visitor_id, conv_id, visitor_key) — "X → reopen"
    // will use the optimistic path to restore the session. Only clear sessionStorage.
    safeRemove('bbc_widget')
    // Reset auto-open guards so attention grabber can trigger again after close.
    formFlowStartedRef.current = false
    intentFiredRef.current = false
    autoOpenedRef.current = false
  }

  // Escape key closes form/chat
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && step !== 'buttons') {
        setStep('buttons')
        safeRemove('bbc_widget')
        formFlowStartedRef.current = false
        intentFiredRef.current = false
        autoOpenedRef.current = false
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [step])

  return (
    <>
      {step === 'buttons' && (
        <FloatingButtons
          onSelect={handleTunnelSelect}
          showAttention={showAttention}
        />
      )}
      {step === 'form' && (
        <div style={{ animation: 'bbc-fadein 0.3s ease' }}>
          <TunnelForm
            tunnel={tunnel}
            onSubmit={handleFormSubmit}
            onBack={handleBack}
            onInteraction={handleFormInteraction}
          />
        </div>
      )}
      {step === 'chat' && (
        <ChatWindow tunnel={tunnel} visitor={visitor} metadata={metadata} onClose={handleCloseChat} apiUrl={apiUrl} />
      )}
    </>
  )
}
