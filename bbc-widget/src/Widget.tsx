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
  // Also clear localStorage — full session state lives there.
  try { localStorage.removeItem('bbc_conv_id') } catch {}
  try { localStorage.removeItem('bbc_conv_ts') } catch {}
  try { localStorage.removeItem('bbc_visitor_key') } catch {}
  try { localStorage.removeItem('bbc_conv_booking_id') } catch {}
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

  // Restore visitor identity from localStorage when sessionStorage has died.
  // `bbc_visitor_key` already stores "name|email|phone" for fingerprint checks
  // in handleFormSubmit — we parse it back so ChatWindow's getValidConvId
  // finds a fingerprint match and restores the conversation instead of
  // creating a new anonymous one. Fixes Bug 6 (visitor identity lost on
  // tab close / page refresh).
  //
  // TODO (privacy hardening): add a lightweight "continue as <partial
  // email>?" confirmation on mount-after-tab-close to block shared-browser
  // leaks (user B auto-restoring user A's session on a shared PC). For
  // BBC's primarily-personal-device user base this risk is accepted in
  // exchange for the frictionless reconnect UX Dan requested on 16.04.2026.
  // Separate ticket — DO NOT remove this TODO or the restore logic below.
  const savedVisitor = (() => {
    if (!hasValidSession) return null
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

  // Restore booking_id from localStorage too — stored by handleFormSubmit
  // when the visitor provided one. Without this, a visitor who entered a
  // booking reference loses it across tab close even though visitor
  // identity is restored.
  const savedMetadata = (() => {
    if (!hasValidSession) return null
    try {
      const bookingId = localStorage.getItem('bbc_conv_booking_id')
      if (!bookingId) return null
      return { booking_id: bookingId }
    } catch { return null }
  })()

  const [step, setStep] = useState<Step>(
    restored?.step === 'chat' || hasValidSession ? 'chat' : 'buttons'
  )
  const [tunnel, setTunnel] = useState<'sales' | 'support'>(
    restored?.tunnel || (hasValidSession ? savedTunnel : 'sales')
  )
  const [visitor, setVisitor] = useState<{ name?: string; email?: string; phone?: string }>(
    // Priority order:
    //   1. sessionStorage (same-tab navigation — freshest)
    //   2. localStorage via savedVisitor (tab-close restore — Bug 6)
    //   3. empty object (new visitor, no session)
    restored?.visitor || savedVisitor || {}
  )
  const [metadata, setMetadata] = useState<{ booking_id?: string }>(
    restored?.metadata || savedMetadata || {}
  )
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
    // NOTE: 'mousedown' intentionally NOT listed — it was too aggressive
    // (any click anywhere on the host page killed auto-open permanently).
    // Touch, focus, and text input are sufficient signals of real engagement.
    window.addEventListener('keydown', markActive, { capture: true })
    window.addEventListener('input', markActive, { capture: true })
    window.addEventListener('change', markActive, { capture: true })
    window.addEventListener('paste', markActive, { capture: true })
    window.addEventListener('touchstart', markActive, { capture: true })
    window.addEventListener('focusin', markActive, { capture: true })
    return () => {
      window.removeEventListener('keydown', markActive, { capture: true })
      window.removeEventListener('input', markActive, { capture: true })
      window.removeEventListener('change', markActive, { capture: true })
      window.removeEventListener('paste', markActive, { capture: true })
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
              // POLICY: auto-open goes to FORM, not chat. Anonymous conversations
              // are no longer permitted per Dan 16.04.2026. Previously the
              // destination was 'chat' with visitor={} — that created anonymous
              // DB rows. DO NOT flip this back without Dan's approval.
              // If a valid 30-min session exists on disk, let handleTunnelSelect
              // handle the restore flow (hasValidSession check there).
              autoOpenedRef.current = true
              setTunnel('sales')
              setStep('form')
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
      // POLICY: same as Effect B — auto-open goes to FORM, not chat.
      // See Bug 2 policy note at top of this prompt.
      autoOpenedRef.current = true
      setTunnel('sales')
      setStep('form')
    }, 3 * 60 * 1000) // 3 minutes

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
    // Persist booking_id in localStorage so it survives tab close (Bug 6).
    // If the visitor didn't provide one, ensure no stale value lingers.
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
    // Keep visitor/metadata in memory — required for "X → reopen" to restore
    // the same conversation. localStorage already persists the conv_id; clearing
    // the in-memory visitor would make ChatWindow mount with visitor={} next
    // time, triggering the anonymous-session path. See Bug 1 forensic.
    safeRemove('bbc_widget')
    // Reset auto-open guards so attention grabber can trigger again after close.
    // Do NOT touch localStorage — the 30-minute session persists on disk.
    userTypingRef.current = false
    formFlowStartedRef.current = false
    intentFiredRef.current = false
    autoOpenedRef.current = false
  }

  // Escape key closes form/chat
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && step !== 'buttons') {
        setStep('buttons')
        // Same reasoning as handleCloseChat: preserve in-memory visitor for
        // session restore. See Bug 1 forensic.
        safeRemove('bbc_widget')
        userTypingRef.current = false
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
