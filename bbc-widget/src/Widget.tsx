import { useState, useEffect, useRef } from 'preact/hooks'
import { FloatingButtons } from './FloatingButtons'
import { TunnelForm } from './TunnelForm'
import { ChatWindow, type Unread } from './ChatWindow'
import { armChime, playChime } from './chime'
import { readPageContext } from './pageContext'
import { getVisitorId } from './api'
import { getUtm } from './utm'
import brand from './config'
import { usePhoneSheet } from './phoneSheet'

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

export function Widget({ apiUrl, embedded = false }: { apiUrl: string; embedded?: boolean }) {
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
        site: brand.id,
        ...(bookingId ? { booking_id: bookingId } : {}),
      }
    } catch { return null }
  })()

  // A conversation this visitor already has (conversation id and identity cached): it is kept connected
  // in the background on every page, so nothing it says is missed.
  const sessionActive = !!(hasOptimisticSession && savedVisitor)

  // Embedded (CRM iframe): no bubble step — mount straight on the form
  // (or chat when a session restores).
  // On the site the panel opens on load only when this tab had it open (sessionStorage), so a visitor who
  // closed the chat is not handed it again on every page. A returning visitor's conversation is still
  // there: a tap on the button restores it (handleTunnelSelect). The CRM iframe keeps the old rule.
  const [step, setStep] = useState<Step>(
    restored?.step === 'chat' || (embedded && hasOptimisticSession && savedVisitor)
      ? 'chat'
      : embedded ? 'form' : 'buttons'
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
  const [focusLauncher, setFocusLauncher] = useState(false)
  /** What the visitor is looking at (the page's h1): the greeting speaks to it, the chat starts from it. */
  const [pageCtx] = useState(readPageContext)
  /** Messages that arrived while the chat was closed (or the tab hidden), with the latest for a preview. */
  const [unread, setUnread] = useState<Unread>({ count: 0, fresh: false })
  /** Bumped when the backend says the cached conversation is over, so the kept-alive chat unmounts. */
  const [, setSessionTick] = useState(0)
  /** The chat replaced the form in an open panel: no second entrance, only the conversation fades in. */
  const [swapped, setSwapped] = useState(false)
  /** The panel is on its way out (a short exit animation before it unmounts). */
  const [closing, setClosing] = useState(false)
  const closingRef = useRef(false)

  /** Close with the exit animation; reduced motion and the CRM iframe close at once. */
  const animateClose = (done: () => void) => {
    if (closingRef.current) return
    let instant = embedded
    try { instant = instant || window.matchMedia('(prefers-reduced-motion: reduce)').matches } catch { /* old browser */ }
    if (instant) return done()
    closingRef.current = true
    setClosing(true)
    setTimeout(() => {
      closingRef.current = false
      setClosing(false)
      done()
    }, 220)
  }

  // Auto-open refs
  const autoOpenedRef = useRef(false)
  const formFlowStartedRef = useRef(false)

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
          setSessionTick(t => t + 1)
          // Only fall back if user hasn't navigated away from chat already.
          // Embedded: there is no bubble to fall back to — show the form.
          setStep(prev => prev === 'chat' ? (embedded ? 'form' : 'buttons') : prev)
        }
      })
      .catch(() => {
        // Network error — keep optimistic UI (don't disrupt active session)
      })
    return () => { cancelled = true }
  // eslint-disable-next-line
  }, [])

  // ─── The greeting beside the button ─────────────────────────────────────────
  // Once per visit (sessionStorage), 10 s after load, a short note appears beside the corner button. It covers
  // nothing and opens the chat only when tapped (it replaced the form that opened by itself after 10 s on
  // computers and 3 min on phones, and the mouse-dwell opener: the site's rule is that nothing opens on load).
  // A visitor already in a conversation gets news of it on the button instead (unread), never this.
  // On computers the greeting also comes early when the pointer leaves the page through the top
  // (towards the tabs or the address bar): the moment a visitor is about to go.
  useEffect(() => {
    if (embedded || step !== 'buttons' || sessionActive) return
    if (autoOpenedRef.current || formFlowStartedRef.current) return
    if (safeGet('bbc_attention_shown') === '1') return

    const show = () => {
      if (formFlowStartedRef.current || autoOpenedRef.current) return
      autoOpenedRef.current = true
      safeSet('bbc_attention_shown', '1')
      setShowAttention(true)
    }
    const attentionTimer = setTimeout(show, 10_000)
    const loadedAt = Date.now()
    const onLeave = (e: MouseEvent) => {
      if (e.relatedTarget || e.clientY > 8 || Date.now() - loadedAt < 3000) return
      show()
    }
    let fine = false
    try { fine = window.matchMedia('(pointer: fine)').matches } catch { /* old browser */ }
    if (fine) document.documentElement.addEventListener('mouseleave', onLeave)

    return () => {
      clearTimeout(attentionTimer)
      document.documentElement.removeEventListener('mouseleave', onLeave)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step])

  // Keep an open chat open across this tab's pages (also when it was restored by a tap on the button).
  useEffect(() => {
    if (embedded || step !== 'chat') return
    safeSet('bbc_widget', JSON.stringify({ step: 'chat', tunnel, visitor, metadata }))
  }, [step, tunnel])

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
      site: brand.id,
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
    setSwapped(true)
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
    if (embedded) return  // no bubble to go back to; ✕ is hidden anyway
    animateClose(() => {
      setFocusLauncher(true)
      setSwapped(false)
      setStep('buttons')
      clearWidgetStorage()
    })
  }

  const handleCloseChat = () => {
    if (embedded) return  // panel is always open inside the CRM iframe
    animateClose(() => {
      setFocusLauncher(true)
      setSwapped(false)
      setStep('buttons')
    })
    // Keep localStorage intact (visitor_id, conv_id, visitor_key) — "X → reopen"
    // will use the optimistic path to restore the session. Only clear sessionStorage.
    safeRemove('bbc_widget')
    // Reset auto-open guards so attention grabber can trigger again after close.
    formFlowStartedRef.current = false
    autoOpenedRef.current = false
  }

  // Escape key closes form/chat (site mode only — embedded panel stays open)
  useEffect(() => {
    if (embedded) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && step !== 'buttons') {
        animateClose(() => {
          setFocusLauncher(true)
          setSwapped(false)
          setStep('buttons')
        })
        safeRemove('bbc_widget')
        formFlowStartedRef.current = false
        autoOpenedRef.current = false
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [step])

  usePhoneSheet(step !== 'buttons', embedded, brand.colors.ink)

  // A conversation in progress stays connected for the whole visit, open or closed, on every page of
  // the site: what is being sent finishes, replies keep arriving, and the corner button can announce them.
  const chatMounted = step === 'chat' || (!embedded && sessionActive)
  useEffect(() => { if (chatMounted) armChime() }, [chatMounted])

  const handleUnread = (u: Unread) => {
    setUnread(u)
    if (u.fresh) playChime()
  }

  // A tab in the background says so in its title: "(1) New message · <the page's own title>".
  const baseTitleRef = useRef<string | null>(null)
  useEffect(() => {
    const apply = () => {
      try {
        const hidden = document.visibilityState !== 'visible'
        if (unread.count > 0 && hidden) {
          if (baseTitleRef.current === null) baseTitleRef.current = document.title
          document.title = `(${unread.count}) New message · ${baseTitleRef.current}`
        } else if (baseTitleRef.current !== null) {
          document.title = baseTitleRef.current
          baseTitleRef.current = null
        }
      } catch { /* no document title to touch */ }
    }
    apply()
    document.addEventListener('visibilitychange', apply)
    return () => document.removeEventListener('visibilitychange', apply)
  }, [unread.count])

  return (
    <div class={closing ? 'bw-closing' : undefined} style={{ display: 'contents' }}>
      {step === 'buttons' && !embedded && (
        <FloatingButtons
          onSelect={handleTunnelSelect}
          showAttention={showAttention && !sessionActive}
          onDismissAttention={() => setShowAttention(false)}
          autoFocus={focusLauncher}
          context={pageCtx}
          unread={unread}
        />
      )}
      {step === 'form' && (
        <TunnelForm
          tunnel={tunnel}
          onSubmit={handleFormSubmit}
          onBack={handleBack}
          onInteraction={handleFormInteraction}
          embedded={embedded}
        />
      )}
      {chatMounted && (
        <ChatWindow
          tunnel={tunnel}
          visitor={visitor}
          metadata={metadata}
          onClose={handleCloseChat}
          apiUrl={apiUrl}
          embedded={embedded}
          swapped={swapped}
          visible={step === 'chat'}
          draft={pageCtx.draft}
          onUnread={handleUnread}
        />
      )}
    </div>
  )
}
