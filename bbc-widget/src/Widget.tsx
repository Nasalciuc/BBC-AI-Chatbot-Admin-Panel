import { useState, useEffect, useRef } from 'preact/hooks'
import { FloatingButtons } from './FloatingButtons'
import { TunnelForm, type FormCheck } from './TunnelForm'
import { ChatWindow, type Unread } from './ChatWindow'
import { armChime, playChime } from './chime'
import { readPageContext } from './pageContext'
import { apiFetch, clearVisitorToken, ensureVisitorId, ensureVisitorToken, getVisitorId, makeVisitorId } from './api'
import { getUtm } from './utm'
import brand from './config'
import { usePhoneSheet } from './phoneSheet'
import { posthog } from './posthog'
import {
  forgetProactiveConversation, isProactiveConversation, proactiveChatStarted, proactiveMetadata, useProactive,
  type ProactiveOpening,
} from './proactive'
import { keepOpening, readKeptOpening } from './keptOpening'

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

function clearWidgetStorage() {
  safeRemove('bbc_widget')       // sessionStorage
  // Clear localStorage session cache (but NOT bbc_visitor_id — that persists forever)
  try { localStorage.removeItem('bbc_conv_id') } catch {}
  try { localStorage.removeItem('bbc_conv_ts') } catch {}
  try { localStorage.removeItem('bbc_visitor_key') } catch {}
  try { localStorage.removeItem('bbc_conv_booking_id') } catch {}
  forgetProactiveConversation()
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

  // A conversation the proactive card opened has no form behind it (no visitor key), and it is still this
  // visitor's conversation: the button brings it back, and it stays connected while closed.
  const proactiveConv = isProactiveConversation(cachedConvId)

  // The card's chat the visitor had not answered on the page before (kept with the open panel, below): the
  // same words come back, and the conversation still starts with their answer. Never over a conversation.
  const keptOpening = cachedConvId ? null : readKeptOpening(restored?.opening)

  // A conversation this visitor already has (conversation id and identity cached): it is kept connected
  // in the background on every page, so nothing it says is missed.
  const sessionActive = !!(hasOptimisticSession && (savedVisitor || proactiveConv))

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
  /** The opening of the chat the card opened: its words, replies and the page's trip. */
  const [opening, setOpening] = useState<ProactiveOpening | null>(keptOpening)

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
    // Refresh the visitor token first: a returning visitor's may be past its
    // 24 h, and with VISITOR_TOKEN_ENFORCE on the check would be refused, which
    // reads like "no active conversation" and drops a live chat.
    ensureVisitorToken(apiUrl)
      .then(() => apiFetch(`${apiUrl}/api/chat/visitor/${encodeURIComponent(visitorId)}/active-conversation`))
      .then(r => {
        // A refusal or a server error says nothing about the conversation:
        // keep the optimistic UI, as on a network error.
        if (!r.ok) throw new Error(`verify failed: ${r.status}`)
        return r.json()
      })
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
  // The card's chat not answered yet keeps its opening: without it the next page would open a chat with
  // nothing behind it, and ChatWindow would start one on the server (/chat/start) that nobody answered.
  useEffect(() => {
    if (embedded || step !== 'chat') return
    const kept = opening && !proactiveChatStarted() ? { opening: keepOpening(opening) } : {}
    safeSet('bbc_widget', JSON.stringify({ step: 'chat', tunnel, visitor, metadata, ...kept }))
  }, [step, tunnel, opening])

  // A tap on the note waits for Alex's words (proactive.noteOpening); whatever the visitor opens or closes
  // meanwhile wins. Every flow that starts takes the turn, and a note answer that lost it does nothing:
  // it neither replaces the form the visitor opened with the chat nor selects the tunnel again.
  const noteRunRef = useRef(0)
  const handleTunnelSelect = (t: 'sales' | 'support', via: 'button' | 'note' = 'button') => {
    noteRunRef.current++
    posthog?.capture('chat_tunnel_selected', {
      tunnel: t,
      returning_visitor: hasOptimisticSession && !!savedVisitor,
      via,
    })
    setShowAttention(false)
    formFlowStartedRef.current = true
    autoOpenedRef.current = true
    setTunnel(t)

    // Skip the form if the visitor completed it before (name+email+phone), or the card opened their chat
    if (hasOptimisticSession && (savedVisitor || proactiveConv)) {
      setStep('chat')
    } else {
      setStep('form')
    }
  }

  const handleFormInteraction = () => {
    formFlowStartedRef.current = true
    autoOpenedRef.current = true
  }

  // PR-12: the form's trap (hidden field + fill time) rides the first
  // /chat/start. Kept in memory only — it describes this one submit.
  const [formCheck, setFormCheck] = useState<FormCheck | undefined>(undefined)

  const handleFormSubmit = (data: { name?: string; email?: string; phone?: string; country_code?: string; booking_id?: string; form_check?: FormCheck }) => {
    setFormCheck(data.form_check)
    // Ensure visitor_id exists before entering chat.
    let visitorId = ensureVisitorId()

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
      posthog?.reset()
      visitorId = makeVisitorId()
      try { localStorage.setItem('bbc_visitor_id', visitorId) } catch {}
      clearVisitorToken()  // PR-12: the old token proves the old id only
      try { localStorage.removeItem('bbc_conv_id') } catch {}
      try { localStorage.removeItem('bbc_conv_ts') } catch {}
    }
    // Only the anonymous visitor id: the client's name, email and phone stay
    // with us and never go to PostHog.
    posthog?.identify(visitorId)
    posthog?.capture('contact_form_submitted', {
      tunnel,
      has_booking_id: Boolean(data.booking_id),
    })
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

  // ─── The chat that opens by itself (proactive.ts) ─────────────────────────────────────────────────
  // At the visitor's moment the server may answer with words for their own search: a card above the
  // button on a phone, the panel with those words on a computer. No form, and nothing is written until
  // the visitor answers. The control group keeps the note above exactly as it is.
  const proactive = useProactive({ apiUrl, step, allowed: !embedded && !hasOptimisticSession, restored: keptOpening })
  const openProactive = (o: ProactiveOpening) => {
    noteRunRef.current++
    proactive.opened()
    formFlowStartedRef.current = true
    setShowAttention(false)
    setTunnel('sales')
    setMetadata(proactiveMetadata())
    setOpening(o)
    setSwapped(false)
    setStep('chat')
  }
  useEffect(() => {
    if (proactive.offer?.surface === 'panel') openProactive(proactive.offer)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [proactive.offer])

  // The 10 s note promises a conversation ("Tell me where and when, and an agent will check fares"): a tap on it
  // opens the chat with Alex's words for this page and no form, like the card's chat (owner's decision,
  // 16 Nov: from 13 Nov, 0 of the 35 visitors who opened the chat got past the form the note led to).
  // The corner button does the same (owner's decision, 16 Nov, later: "straight into the conversation,
  // not with a form"); `via` tells the two apart in the measurements. The words come from the server
  // (the note's door); no answer in time → the form, as before. The note stays on screen until the chat
  // opens, and a second tap of either meanwhile does nothing.
  const noteBusyRef = useRef(false)
  const openNote = async (via: 'note' | 'button' = 'note') => {
    if (noteBusyRef.current) return
    noteBusyRef.current = true
    const run = ++noteRunRef.current
    const stillMine = () => run === noteRunRef.current
    try {
      const o = await proactive.noteOpening(stillMine, via)
      // The visitor opened the form, or closed what was open, while the words were on their way: theirs.
      if (!stillMine()) return
      if (o) {
        posthog?.capture('chat_tunnel_selected', { tunnel: 'sales', returning_visitor: false, via, form: false })
        openProactive(o)
      } else {
        handleTunnelSelect('sales', via)
      }
    } finally {
      noteBusyRef.current = false
    }
  }

  // The corner button: a visitor with a conversation gets it back (handleTunnelSelect, as before); anyone
  // else goes straight into a conversation with Alex, no form first. Support keeps its form (the ticket
  // number comes first there).
  const openFromButton = (t: 'sales' | 'support') => {
    if (t !== 'sales' || sessionActive) {
      handleTunnelSelect(t)
      return
    }
    void openNote('button')
  }

  const handleBack = () => {
    if (embedded) return  // no bubble to go back to; ✕ is hidden anyway
    noteRunRef.current++
    animateClose(() => {
      setFocusLauncher(true)
      setSwapped(false)
      setStep('buttons')
      clearWidgetStorage()
    })
  }

  const handleCloseChat = () => {
    if (embedded) return  // panel is always open inside the CRM iframe
    noteRunRef.current++
    posthog?.capture('chat_closed', { tunnel })
    animateClose(() => {
      setFocusLauncher(true)
      setSwapped(false)
      setStep('buttons')
      setOpening(null)
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
        // The card's chat left unanswered: the same 24 h of quiet as its ✕ and "Not now".
        if (opening && !proactiveChatStarted()) proactive.dismiss('close')
        animateClose(() => {
          setFocusLauncher(true)
          setSwapped(false)
          setStep('buttons')
          setOpening(null)
        })
        safeRemove('bbc_widget')
        formFlowStartedRef.current = false
        autoOpenedRef.current = false
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [step, opening])

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
          onSelect={(t) => (proactive.offer ? openProactive(proactive.offer) : openFromButton(t))}
          showAttention={showAttention && !sessionActive && !proactive.quiet}
          onDismissAttention={() => { noteRunRef.current++; setShowAttention(false) }}
          autoFocus={focusLauncher}
          context={pageCtx}
          unread={unread}
          card={proactive.offer?.surface === 'card' ? proactive.offer : null}
          onCardOpen={(reply) => { if (proactive.offer) openProactive({ ...proactive.offer, firstReply: reply }) }}
          onCardDismiss={() => proactive.dismiss('not_now')}
          onNoteOpen={() => { if (proactive.offer) openProactive(proactive.offer); else void openNote() }}
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
          formCheck={formCheck}
          onClose={handleCloseChat}
          apiUrl={apiUrl}
          embedded={embedded}
          swapped={swapped}
          visible={step === 'chat'}
          draft={pageCtx.draft}
          onUnread={handleUnread}
          proactive={opening}
          onProactiveDismiss={(how) => proactive.dismiss(how)}
          onProactiveEngaged={(reply) => proactive.engaged(reply)}
        />
      )}
    </div>
  )
}
