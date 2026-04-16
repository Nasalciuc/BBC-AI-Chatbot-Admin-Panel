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
  const [showAttention, setShowAttention] = useState(false)

  // Auto-open: after 10 seconds of inactivity, open chat directly for passive visitors.
  // If user enters the form flow and starts typing, auto-open is cancelled and
  // the standard form -> Start Chat procedure applies.
  const autoOpenedRef = useRef(false)
  const userTypingRef = useRef(false)
  const formFlowStartedRef = useRef(false)

  // Intent detection refs
  const intentFiredRef = useRef(false)
  const intentDwellTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const intentChatTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const cachedBtnRect = useRef<DOMRect | null>(null)

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

  // Track user activity on the entire page.
  // Any interaction means visitor is not passive, so auto-chat must not trigger.
  useEffect(() => {
    const markActive = () => { userTypingRef.current = true }
    window.addEventListener('keydown', markActive, { capture: true })
    window.addEventListener('input', markActive, { capture: true })
    window.addEventListener('change', markActive, { capture: true })
    window.addEventListener('paste', markActive, { capture: true })
    window.addEventListener('mousedown', markActive, { capture: true })
    window.addEventListener('touchstart', markActive, { capture: true })
    window.addEventListener('focusin', markActive, { capture: true })
    return () => {
      window.removeEventListener('keydown', markActive, { capture: true })
      window.removeEventListener('input', markActive, { capture: true })
      window.removeEventListener('change', markActive, { capture: true })
      window.removeEventListener('paste', markActive, { capture: true })
      window.removeEventListener('mousedown', markActive, { capture: true })
      window.removeEventListener('touchstart', markActive, { capture: true })
      window.removeEventListener('focusin', markActive, { capture: true })
    }
  }, [])

  // ─── EFFECT A: Attention Grabber (20 secunde inactivitate) ───────────────────
  useEffect(() => {
    if (step !== 'buttons') return
    if (autoOpenedRef.current || formFlowStartedRef.current) return
    if (safeGet('bbc_attention_shown') === '1') return

    const attentionTimer = setTimeout(() => {
      if (formFlowStartedRef.current || userTypingRef.current) return
      setShowAttention(true)
      safeSet('bbc_attention_shown', '1')

      // Badge dispare după 8 secunde
      setTimeout(() => setShowAttention(false), 8_000)
    }, 20_000)

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
      // Nu mai triggereăm dacă: deja fired, form flow, sau typing
      if (intentFiredRef.current || formFlowStartedRef.current || userTypingRef.current) return

      const rect = cachedBtnRect.current
      if (!rect) return

      // Distanța față de centrul butonului
      const dist = Math.hypot(
        e.clientX - (rect.left + rect.width / 2),
        e.clientY - (rect.top + rect.height / 2)
      )

      if (dist < 180) {
        // User e aproape de buton — dacă nu avem deja dwell timer, pornim unul
        if (!intentDwellTimer.current) {
          intentDwellTimer.current = setTimeout(() => {
            // 1.5s dwell confirmat — intent real detectat
            if (formFlowStartedRef.current || userTypingRef.current) return
            intentFiredRef.current = true

            // Timer 10s: dacă nu intră în form → deschidem chat
            intentChatTimer.current = setTimeout(() => {
              if (formFlowStartedRef.current || userTypingRef.current) return
              autoOpenedRef.current = true
              setTunnel('sales')
              setVisitor({})
              setMetadata({})
              setStep('chat')
              safeSet('bbc_widget', JSON.stringify({
                step: 'chat', tunnel: 'sales', visitor: {}, metadata: {}
              }))
            }, 10_000)
          }, 1_500)
        }
        // ONE-WAY: nu anulăm timer-ul la ieșirea din zonă
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
      if (formFlowStartedRef.current || userTypingRef.current) return
      autoOpenedRef.current = true
      setTunnel('sales')
      setVisitor({})
      setMetadata({})
      setStep('chat')
      safeSet('bbc_widget', JSON.stringify({
        step: 'chat', tunnel: 'sales', visitor: {}, metadata: {}
      }))
    }, 3 * 60 * 1000) // 3 minute

    return () => clearTimeout(mobileTimer)
  }, [step])

  const handleTunnelSelect = (t: 'sales' | 'support') => {
    setShowAttention(false)  // ← reset badge
    formFlowStartedRef.current = true
    autoOpenedRef.current = true // user intentionally opened widget; cancel auto-open logic
    setTunnel(t)

    // If valid session exists, skip form and restore chat directly
    if (hasValidSession) {
      setStep('chat')
    } else {
      setStep('form')
    }
  }

  const handleFormInteraction = () => {
    // Once user starts filling the form, keep standard manual flow only.
    formFlowStartedRef.current = true
    autoOpenedRef.current = true
    userTypingRef.current = true
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
      {step === 'buttons' && (
        <FloatingButtons
          onSelect={handleTunnelSelect}
          showAttention={showAttention}
        />
      )}
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
