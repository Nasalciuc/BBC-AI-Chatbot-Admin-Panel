import { useState, useEffect, useRef } from 'preact/hooks'
import { apiFetch, getVisitorId } from './api'
import brand from './config'
import { promoteTemp, reconcileBatch } from './reconcile'
import { PanelTop } from './PanelTop'
import { IconArrowDown, IconArrowUp } from './icons'
import { richText } from './richText'

interface Message {
  id: string
  role: 'user' | 'ai' | 'agent' | 'system'
  content: string
  created_at: string
  /** Operator identity — present on role='agent' messages only. */
  agent_name?: string | null
  agent_avatar_url?: string | null
}

const CONSULTANT_LABEL = 'Consultant'

function agentInitials(name: string): string {
  return name
    .split(' ')
    .map(w => w[0] || '')
    .join('')
    .toUpperCase()
    .slice(0, 2)
}

interface Props {
  tunnel: 'sales' | 'support'
  visitor: { name?: string; email?: string; phone?: string; country_code?: string }
  metadata?: { booking_id?: string }
  onClose: () => void
  apiUrl: string
  /** CRM iframe mode: fill the container, hide the close ✕ (panel is always open). */
  embedded?: boolean
}

/** Read cached conv_id from localStorage. Widget.tsx has already verified
 *  it with the backend on mount — ChatWindow trusts the cache. */
function getCachedConvId(): string | null {
  try { return localStorage.getItem('bbc_conv_id') } catch { return null }
}

export function ChatWindow({ tunnel, visitor, metadata, onClose, apiUrl, embedded = false }: Props) {
  const savedConvId = getCachedConvId()

  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [streamingText, setStreamingText] = useState('')
  const [isStreaming, setIsStreaming] = useState(false)
  const isStreamingRef = useRef(false)
  const [quickReplies, setQuickReplies] = useState<string[] | null>(null)
  const [pendingGreeting, setPendingGreeting] = useState(false)
  const [convId, setConvId] = useState<string | null>(savedConvId)
  const logRef = useRef<HTMLDivElement>(null)
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const initialized = useRef(false)
  const typingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastTypingSentRef = useRef<number>(0)
  const closeSentRef = useRef(false)
  // Last time the client actually DID something in the chat. A tab left
  // open on a desk is visible, not present — the ping stops after a
  // stretch of real idleness so the server can age the state down.
  const lastInteractionRef = useRef(Date.now())
  /** Operator-typing state, polled from the backend (name only, never the draft). */
  const [agentTyping, setAgentTyping] = useState<{ is_typing: boolean; name: string }>({
    is_typing: false,
    name: '',
  })
  /** Messages written while the assistant was still answering: they go out, in order, as soon as it finishes. */
  const [queued, setQueued] = useState<{ id: string; text: string }[]>([])
  /** The greeting is on its way (fresh sessions): the log shows the assistant typing instead of nothing. */
  const [starting, setStarting] = useState(!savedConvId)
  /** The visitor scrolled up to read: new messages no longer pull the log down; a button offers to. */
  const atBottomRef = useRef(true)
  const [hasUnseen, setHasUnseen] = useState(false)
  /** Operator photos that failed to load → show initials instead of a broken image. */
  const [brokenAvatars, setBrokenAvatars] = useState<Record<string, boolean>>({})

  // Safety net: force-unlock sending after 15s (protects against any stuck state)
  useEffect(() => {
    if (!sending) return
    const timeout = setTimeout(() => {
      setSending(false)
      setIsStreaming(false)
      isStreamingRef.current = false
    }, 15000)
    return () => clearTimeout(timeout)
  }, [sending])

  const notifySessionClose = (reason: 'minimized' | 'left', keepalive = false) => {
    if (!convId || closeSentRef.current) return
    closeSentRef.current = true
    apiFetch(`${apiUrl}/api/chat/session/${convId}/close?reason=${reason}`, {
      method: 'POST',
      keepalive,
    }).catch(() => {})
  }

  // Keep the newest message in view. Scrolls the log itself, never the host page (scrollIntoView would
  // also scroll the site behind the panel on some phones).
  // A visitor reading back up the conversation is never yanked down: the log follows new messages only
  // while it is at the bottom, and otherwise shows a "New message" button.
  const scrollToEnd = (smooth = true) => {
    const el = logRef.current
    if (!el) return
    atBottomRef.current = true
    setHasUnseen(false)
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' })
  }
  const onLogScroll = () => {
    const el = logRef.current
    if (!el) return
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < 72
    atBottomRef.current = near
    if (near) setHasUnseen(false)
  }
  useEffect(() => {
    if (atBottomRef.current) scrollToEnd(messages.length > 1)
    else setHasUnseen(true)
  }, [messages.length, streamingText, sending, agentTyping.is_typing, queued.length])

  // The composer takes focus on computers; on touch screens the keyboard waits for a tap.
  useEffect(() => {
    try {
      if (window.matchMedia('(pointer: fine)').matches) composerRef.current?.focus({ preventScroll: true })
    } catch { /* matchMedia unavailable — leave focus where it is */ }
  }, [])

  /** Grow the composer with its text, up to its max height (CSS). */
  const fitComposer = () => {
    const el = composerRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight + 2}px`
  }

  // The AI greets FIRST: create the conversation and render the armed,
  // personalized opening as the first bubble. No fake client message.
  const startConversation = async () => {
    try {
      const res = await apiFetch(`${apiUrl}/api/chat/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tunnel,
          visitor: {
            name: visitor.name || null,
            email: visitor.email || null,
            phone: visitor.phone || null,
            country_code: visitor.country_code || null,
          },
          metadata: metadata || {},
          visitor_id: getVisitorId(),
        }),
      })
      if (!res.ok) throw new Error('start failed')
      const data = await res.json()
      if (!data.conversation_id) return
      setConvId(data.conversation_id)
      apiFetch(`${apiUrl}/api/chat/session/${data.conversation_id}/open`, { method: 'POST' }).catch(() => {})
      try { localStorage.setItem('bbc_conv_id', data.conversation_id) } catch {}
      try { localStorage.setItem('bbc_conv_ts', Date.now().toString()) } catch {}
      if (data.message) {
        const aiMsg: Message = {
          id: `ai-${Date.now()}`,
          role: 'ai',
          content: data.message,
          created_at: new Date().toISOString(),
        }
        setMessages(prev => [...prev, aiMsg])
        // Checkpoint past the greeting so the next poll doesn't duplicate it
        lastMsgTime.current = new Date(Date.now() + 2000).toISOString()
      }
    } catch {
      // Fail quiet — the client can still type; their first message flows
      // through the normal send path and creates the conversation.
    } finally {
      setStarting(false)
    }
  }

  // AI opens on fresh sessions — SKIP if restored session
  useEffect(() => {
    if (initialized.current) return
    initialized.current = true
    if (savedConvId) return
    startConversation()
  // eslint-disable-next-line
  }, [])

  // Verify restored session is still active (runs once at mount)
  useEffect(() => {
    if (!savedConvId) return
    apiFetch(`${apiUrl}/api/chat/session/${savedConvId}/open`, { method: 'POST' }).catch(() => {})
    fetch(`${apiUrl}/api/chat/status/${savedConvId}`)
      .then(r => r.json())
      .then(data => {
        if (!data.success || data.data?.status === 'closed') {
          // Conversation closed by agent — but client can still write within 30min session.
          // Next message will automatically reopen the conversation on the backend.
          // Keep localStorage intact so session persists.
          // Do nothing — let client see history and write freely.
        }
      })
      .catch(() => { /* network error — keep session, polling will handle */ })
  // eslint-disable-next-line
  }, [])

  // Re-open with an AI greeting after a closed conversation was detected and cleared
  useEffect(() => {
    if (!pendingGreeting || convId || sending) return
    setPendingGreeting(false)
    startConversation()
  }, [pendingGreeting, convId, sending])

  // Real-time delivery: SSE primary + polling fallback (RxDB checkpoint pattern)
  const lastMsgTime = useRef('')

  useEffect(() => {
    if (!convId) return
    closeSentRef.current = false

    const handlePageLeave = () => notifySessionClose('left', true)
    // bfcache return (back button, mobile app-switch): pagehide fired 'left'
    // on the way out — mark the client back and re-arm the close beacon.
    const handlePageReturn = () => {
      closeSentRef.current = false
      apiFetch(`${apiUrl}/api/chat/session/${convId}/open`, { method: 'POST' }).catch(() => {})
    }
    window.addEventListener('pagehide', handlePageLeave)
    window.addEventListener('beforeunload', handlePageLeave)
    window.addEventListener('pageshow', handlePageReturn)

    // PRESENCE HEARTBEAT — the close beacon is best-effort: a killed tab,
    // a suspended phone or a dropped network loses it, and the panel then
    // shows "online" for someone who left an hour ago. A ping every 30s
    // while the tab is VISIBLE means silence is itself the signal: two
    // missed pings and the server derives 'stale' on read. Presence-only
    // write — it never bumps updated_at (migration 029).
    const PING_MS = 30_000
    // A tab left open on a desk overnight is visible, not present. After
    // this long without the client touching the chat, stop claiming they
    // are there and let the server age the state down honestly.
    const PING_IDLE_CUTOFF_MS = 15 * 60_000
    let pingInterval: ReturnType<typeof setInterval> | null = null
    const sendPing = () => {
      if (document.visibilityState !== 'visible') return
      if (Date.now() - lastInteractionRef.current > PING_IDLE_CUTOFF_MS) return
      apiFetch(`${apiUrl}/api/chat/session/${convId}/ping`, { method: 'POST' }).catch(() => {})
    }
    const startPing = () => {
      if (pingInterval) return
      sendPing()
      pingInterval = setInterval(sendPing, PING_MS)
    }
    const stopPing = () => {
      if (!pingInterval) return
      clearInterval(pingInterval)
      pingInterval = null
    }
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') startPing()
      else stopPing()
    }
    document.addEventListener('visibilitychange', handleVisibility)
    startPing()

    let source: EventSource | null = null
    let fallbackInterval: ReturnType<typeof setInterval> | null = null
    let errorCount = 0
    const MAX_ERRORS = 3

    // Catch-up poll — syncs missed messages after SSE connect/reconnect
    const catchUpPoll = async () => {
      if (document.visibilityState !== 'visible') return
      try {
        const after = lastMsgTime.current
          ? `?after=${encodeURIComponent(lastMsgTime.current)}`
          : ''
        const res = await fetch(`${apiUrl}/api/chat/messages/${convId}${after}`, {
          headers: { 'X-Visitor-Id': getVisitorId() || '' },
        })
        if (!res.ok) return
        const json = await res.json()
        if (json.success && json.data && json.data.length > 0) {
          const incoming = json.data as Message[]
          setMessages(prev => {
            // Reconcile (src/reconcile.ts): the poll may EXPIRE only stale
            // temps — a confirmed send lives as `sent-*` until its server
            // row arrives in a batch and replaces it. The old "drop all
            // temps, hope the batch has them" erased the client's own
            // message whenever the agent-joined burst advanced the ?after=
            // cursor past its timestamp.
            const { next, sawAi } = reconcileBatch(prev, incoming, Date.now())
            if (sawAi) {
              setIsStreaming(false)
              isStreamingRef.current = false
              setSending(false)
            }
            return next
          })
          lastMsgTime.current = json.data[json.data.length - 1].created_at
        }
      } catch { /* non-fatal */ }
    }

    // Polling fallback — activated permanently after 3 SSE errors
    const startPolling = () => {
      if (fallbackInterval) return
      fallbackInterval = setInterval(catchUpPoll, 1000)
    }

    // SSE stream — primary real-time channel
    const startSSE = () => {
      source = new EventSource(`${apiUrl}/api/chat/stream/${convId}?vid=${encodeURIComponent(getVisitorId() || '')}`)

      source.onopen = () => {
        errorCount = 0        // reset error counter on successful connect
        catchUpPoll()         // sync messages missed during connect/reconnect
      }

      source.onmessage = (e: MessageEvent) => {
        try {
          const parsed = JSON.parse(e.data)

          if (parsed.event === 'stream_chunk') {
            setStreamingText(prev => prev + (parsed.delta || ''))
            return
          }

          if (parsed.event === 'stream_end') {
            setStreamingText('')
            setIsStreaming(false)
            isStreamingRef.current = false
            setSending(false)
            const msg = parsed as Message
            setMessages(prev => {
              if (prev.some(m => m.id === msg.id)) return prev
              return [...prev, msg]
            })
            if (msg.created_at) lastMsgTime.current = msg.created_at
            return
          }

          // The stream also carries events that are not messages: the visitor's
          // own typing echoed back ({event:'typing'}), presence, and the like.
          // They have no id and no text; taken as messages they rendered as
          // blank bubbles and, since the redesign, crashed the render (the
          // send button never lit up). Only stored message rows go on.
          if (parsed.event || typeof parsed.id !== 'string' || typeof parsed.content !== 'string') return

          const msg = parsed as Message
          // The server now fans out the visitor's own message too (so the
          // operator's panel sees it). The widget already rendered it locally
          // under a temp- id that will never match the stored UUID, so taking
          // it from the stream would show it twice. A second tab of the same
          // visitor still gets it through the incremental poll.
          if (msg.role === 'user') return
          setMessages(prev => {
            if (prev.some(m => m.id === msg.id)) return prev
            return [...prev, msg]
          })
          if (msg.created_at) lastMsgTime.current = msg.created_at
        } catch { /* malformed event — ignore */ }
      }

      source.onerror = () => {
        // An HTTP error status (a 502 or 503 while the API redeploys) closes an
        // EventSource for good: the browser never retries and onerror fires once,
        // so the counter below would never reach MAX_ERRORS and operator messages
        // would stop arriving until a reload. Poll instead, at once.
        if (source && source.readyState === EventSource.CLOSED) {
          source = null
          startPolling()
          return
        }
        errorCount++
        if (errorCount >= MAX_ERRORS) {
          // SSE not reliable in this environment — fall back to polling
          source?.close()
          source = null
          startPolling()
        }
        // else: browser retries EventSource connection automatically
      }
    }

    catchUpPoll()  // instant load — don't wait for SSE onopen (1-2s delay)
    startSSE()

    return () => {
      notifySessionClose('minimized', true)
      source?.close()
      if (fallbackInterval) clearInterval(fallbackInterval)
      if (typingTimeoutRef.current) clearTimeout(typingTimeoutRef.current)
      stopPing()
      document.removeEventListener('visibilitychange', handleVisibility)
      window.removeEventListener('pagehide', handlePageLeave)
      window.removeEventListener('beforeunload', handlePageLeave)
      window.removeEventListener('pageshow', handlePageReturn)
    }
  }, [convId, apiUrl])

  // Operator → visitor typing indicator. The backend key has a short TTL, so a
  // stopped operator stops the indicator on its own; the poll only reads.
  useEffect(() => {
    if (!convId) return
    let cancelled = false

    const poll = async () => {
      if (document.visibilityState !== 'visible') return
      try {
        const res = await fetch(`${apiUrl}/api/chat/agent-typing/${convId}`, {
          headers: { 'X-Visitor-Id': getVisitorId() || '' },
        })
        if (!res.ok || cancelled) return
        const json = await res.json()
        if (cancelled) return
        const data = json?.data
        setAgentTyping({
          is_typing: Boolean(data?.is_typing),
          name: data?.name || '',
        })
      } catch { /* non-fatal */ }
    }

    poll()
    const id = setInterval(poll, 1000)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [convId, apiUrl])

  // An arrived operator message means they finished typing — drop the indicator
  // immediately instead of waiting for the next poll.
  const lastMessageId = messages.length ? messages[messages.length - 1].id : ''
  useEffect(() => {
    if (!messages.length) return
    if (messages[messages.length - 1].role === 'agent') {
      setAgentTyping({ is_typing: false, name: '' })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastMessageId])

  // Send typing event to server.
  //
  // Operator sees the client's typed text in real time. Text stays
  // visible until the client sends / clears input / closes the widget.
  // Previously a 3-second auto-clear timer made the text disappear on
  // pauses — removed because it made it hard for the operator to see
  // what the client was writing during natural pauses (especially for
  // long messages).
  //
  // Clear paths still in place:
  //   1. Input cleared by client → DELETE here (immediately below)
  //   2. Message sent → DELETE in sendMessage() and handleSend()
  //   3. Widget closed / unmounted → no explicit call; backend Redis TTL
  //      (5 min) cleans up orphan state.
  const sendTypingEvent = (text: string) => {
    if (!convId) return

    // Input cleared by client → clear indicator immediately.
    if (!text.trim()) {
      if (typingTimeoutRef.current) {
        clearTimeout(typingTimeoutRef.current)
        typingTimeoutRef.current = null
      }
      apiFetch(`${apiUrl}/api/chat/typing/${convId}`, {
        method: 'DELETE',
      }).catch(() => {})
      return
    }

    // Throttle POST — at most every 200ms while typing.
    // This also refreshes the backend Redis TTL so the indicator
    // persists across natural typing pauses.
    const now = Date.now()
    if (now - lastTypingSentRef.current > 200) {
      lastTypingSentRef.current = now
      apiFetch(`${apiUrl}/api/chat/typing/${convId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      }).catch(() => {})
    }

    // NO auto-clear timer — removed to fix the "text disappears when
    // client pauses" bug. See the comment block at top of this function.
  }

  const sendMessage = async (text: string) => {
    if (!text.trim()) return
    lastInteractionRef.current = Date.now()
    // Clear typing indicator immediately when sending
    if (convId) {
      if (typingTimeoutRef.current) clearTimeout(typingTimeoutRef.current)
      apiFetch(`${apiUrl}/api/chat/typing/${convId}`, { method: 'DELETE' }).catch(() => {})
    }
    setSending(true)
    setStreamingText('')

    const optimisticMsg: Message = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    }
    setMessages(prev => [...prev, optimisticMsg])

    try {
      // Sprint 2: Pre-create conv for SSE streaming on first message
      let activeConvId = convId
      if (!activeConvId) {
        try {
          const initRes = await apiFetch(`${apiUrl}/api/chat/init`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              tunnel,
              visitor: {
                name: visitor.name || null,
                email: visitor.email || null,
                phone: visitor.phone || null,
                country_code: visitor.country_code || null,
              },
              metadata: metadata || {},
              visitor_id: getVisitorId(),
            }),
          })
          if (initRes.ok) {
            const initData = await initRes.json()
            const newConvId = initData.conversation_id as string
            activeConvId = newConvId
            setConvId(newConvId)
            apiFetch(`${apiUrl}/api/chat/session/${newConvId}/open`, { method: 'POST' }).catch(() => {})
            try { localStorage.setItem('bbc_conv_id', newConvId) } catch {}
            try { localStorage.setItem('bbc_conv_ts', Date.now().toString()) } catch {}
            // Wait for SSE useEffect to fire and connect
            await new Promise<void>((resolve) => {
              const check = setInterval(() => {
                clearInterval(check)
                resolve()
              }, 400)
            })
          }
        } catch (e) {
          console.warn('[bbc-widget] /init failed, falling back to direct POST', e)
        }
      }

      const res = await apiFetch(`${apiUrl}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: text,
          conversation_id: activeConvId || convId,
          tunnel,
          visitor: {
            name: visitor.name || null,
            email: visitor.email || null,
            phone: visitor.phone || null,
            country_code: visitor.country_code || null,
          },
          metadata: metadata || {},
          visitor_id: getVisitorId(),
        }),
      })

      if (!res.ok) throw new Error('API error')
      const data = await res.json()

      // The 200 IS the save receipt: promote the optimistic bubble to
      // `sent-*` so no poll can ever drop it. When a batch later carries
      // the real user row, reconcileBatch swaps it in (content match).
      setMessages(prev => promoteTemp(prev, optimisticMsg.id, Date.now()))

      if (data.conversation_id && !convId) {
        setConvId(data.conversation_id)
        apiFetch(`${apiUrl}/api/chat/session/${data.conversation_id}/open`, { method: 'POST' }).catch(() => {})
        try { localStorage.setItem('bbc_conv_id', data.conversation_id) } catch {}
        // Save initial timestamp for 30-minute rolling expiry
        try { localStorage.setItem('bbc_conv_ts', Date.now().toString()) } catch {}
      }

      // Handle system_messages from routing response (zero polling duplicates)
      if (data.system_messages && data.system_messages.length > 0) {
        setMessages(prev => [
          // Remove optimistic connecting message
          ...prev.filter(m => m.id !== '__connecting_temp__'),
          // Add real system messages with real UUIDs from backend
          ...data.system_messages.map((sm: any) => ({
            id: sm.id,
            role: 'system' as const,
            content: sm.content,
            created_at: sm.created_at,
          })),
        ])
        // Show quick reply buttons only on new conversations
        if (data.quick_replies && data.quick_replies.length > 0 && !savedConvId) {
          setQuickReplies(data.quick_replies)
        }
        // Update lastMsgTime so next poll doesn't re-fetch user message
        const lastSysTs = data.system_messages[data.system_messages.length - 1]?.created_at
        if (lastSysTs) lastMsgTime.current = lastSysTs
      } else if (data.streaming) {
        if (activeConvId || convId) {
          setIsStreaming(true)
          isStreamingRef.current = true
        }
      } else if (data.message && data.type !== 'queued') {
        const aiMsg: Message = {
          id: `ai-${Date.now()}`,
          role: 'ai',
          content: data.message,
          created_at: new Date().toISOString(),
        }
        setMessages(prev => [...prev, aiMsg])
        lastMsgTime.current = new Date(Date.now() + 2000).toISOString()
      }
      // Extend 30-minute rolling session on every successful message
      try { localStorage.setItem('bbc_conv_ts', Date.now().toString()) } catch {}
    } catch {
      setStreamingText('')
      setIsStreaming(false)
      isStreamingRef.current = false
      // Clean up optimistic message if the request failed
      setMessages(prev => prev.filter(m => m.id !== '__connecting_temp__'))
      const errMsg: Message = {
        id: `err-${Date.now()}`,
        role: 'system',
        content: `Something went wrong. Please try again or call ${brand.contactPhone} for immediate help.`,
        created_at: new Date().toISOString(),
      }
      setMessages(prev => [...prev, errMsg])
    } finally {
      if (!isStreamingRef.current) {
        setSending(false)
      }
    }
  }

  const handleSend = () => {
    const text = input.trim()
    if (!text) return
    atBottomRef.current = true
    if (sending) {
      // The assistant is still answering: one message at a time reaches the server, so this one waits
      // its turn on screen and goes out by itself when the answer is in.
      setQueued(q => [...q, { id: `queued-${Date.now()}`, text }])
      sendTypingEvent('')
    } else {
      sendMessage(text)
    }
    setInput('')
    requestAnimationFrame(() => {
      const el = composerRef.current
      if (el) el.style.height = ''
    })
  }

  // Send what waited, in order, as soon as the assistant has finished.
  useEffect(() => {
    if (sending || queued.length === 0) return
    const [next, ...rest] = queued
    setQueued(rest)
    sendMessage(next.text)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sending, queued])

  /** A send that failed: take it and its error note away, and send the text again. */
  const retry = (msg: Message) => {
    setMessages(prev => prev.filter(m => m.id !== msg.id && !m.id.startsWith('err-')))
    atBottomRef.current = true
    if (sending) setQueued(q => [...q, { id: `queued-${Date.now()}`, text: msg.content }])
    else sendMessage(msg.content)
  }

  const handleQuickReply = (text: string) => {
    setQuickReplies(null)
    sendMessage(text)
  }

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }

  // ── Presentation ─────────────────────────────────────────────────────────
  // Only rows with an id and some text are drawn (a defence in depth: nothing
  // malformed from the server may ever break the render).
  const visible = messages.filter(m =>
    m && typeof m.id === 'string' && typeof m.content === 'string' && m.content.trim() !== '')
  const speaker = (m: Message) =>
    m.role === 'user' ? 'out' : m.role === 'agent' ? `agent:${(m.agent_name || '').trim()}` : m.role
  const timeOf = (iso: string) => {
    try { return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) } catch { return '' }
  }
  const startsGroup = (i: number) => {
    if (i === 0) return true
    const prev = visible[i - 1]
    const cur = visible[i]
    if (speaker(prev) !== speaker(cur)) return true
    return new Date(cur.created_at).getTime() - new Date(prev.created_at).getTime() > 5 * 60_000
  }
  const endsGroup = (i: number) => i === visible.length - 1 || startsGroup(i + 1)
  const lastAgent = [...visible].reverse().find(m => m.role === 'agent')
  const lastAgentName = (lastAgent?.agent_name || '').trim()
  const subtitle = lastAgentName ? `Chatting with ${lastAgentName}` : brand.tagline
  const brandAvatar = <img class="bw-ava bw-ava--brand" src={brand.logoUrl} alt="" width={28} height={28} />
  const aiWaiting = ((sending || isStreaming) && !streamingText) || (starting && visible.length === 0)
  // Day dividers, only for a conversation that is not all from today (a restored one).
  const dayKey = (iso: string) => { const d = new Date(iso); return isNaN(d.getTime()) ? '' : d.toDateString() }
  const today = new Date().toDateString()
  const showDays = visible.some(m => dayKey(m.created_at) && dayKey(m.created_at) !== today)
  const dayLabel = (iso: string) => {
    const d = new Date(iso)
    const yesterday = new Date(Date.now() - 86_400_000).toDateString()
    if (d.toDateString() === today) return 'Today'
    if (d.toDateString() === yesterday) return 'Yesterday'
    return d.toLocaleDateString([], { weekday: 'short', day: 'numeric', month: 'short' })
  }
  const dots = <span class="bw-dots" aria-hidden="true"><i /><i /><i /></span>

  return (
    <div
      class={`bw-panel${embedded ? ' bw-panel--embedded' : ''}`}
      role="dialog"
      aria-modal="false"
      aria-labelledby="bw-chat-title"
    >
      <PanelTop
        titleId="bw-chat-title"
        subtitle={subtitle}
        closeLabel="Close chat"
        onClose={embedded ? undefined : () => { notifySessionClose('minimized', true); onClose() }}
      />

      <div class="bw-logwrap">
      <div ref={logRef} class="bw-log" role="log" aria-live="polite" aria-label="Conversation" onScroll={onLogScroll}>
        {visible.map((msg, i) => {
          const newDay = showDays && (i === 0 || dayKey(visible[i - 1].created_at) !== dayKey(msg.created_at))
          const day = newDay && dayKey(msg.created_at)
            ? <div key={`day-${msg.id}`} class="bw-day">{dayLabel(msg.created_at)}</div>
            : null
          if (msg.role === 'system') {
            return [day,
              <div key={msg.id} class={`bw-note${msg.id.startsWith('err-') ? ' bw-note--error' : ''}`}>
                {richText(msg.content)}
              </div>,
            ]
          }
          const out = msg.role === 'user'
          const first = startsGroup(i)
          const last = endsGroup(i)
          const isOperator = msg.role === 'agent'
          const operatorName = (msg.agent_name || '').trim() || CONSULTANT_LABEL
          return [day,
            <div key={msg.id} class={`bw-row ${out ? 'bw-row--out' : 'bw-row--in'}${first || newDay ? ' bw-row--first' : ''}`}>
              {!out && (first || newDay) && (
                <div class="bw-who">
                  {isOperator
                    ? (msg.agent_avatar_url && !brokenAvatars[msg.id]
                        ? <img
                            class="bw-ava"
                            src={msg.agent_avatar_url}
                            alt=""
                            width={28}
                            height={28}
                            onError={() => setBrokenAvatars(prev => ({ ...prev, [msg.id]: true }))}
                          />
                        : <span class="bw-ava bw-ava--initials" aria-hidden="true">{agentInitials(operatorName)}</span>)
                    : brandAvatar}
                  <span>
                    <b>{isOperator ? operatorName : 'Assistant'}</b>
                    {isOperator && operatorName !== CONSULTANT_LABEL ? `, ${CONSULTANT_LABEL.toLowerCase()}` : ''}
                    {' · '}{timeOf(msg.created_at)}
                  </span>
                </div>
              )}
              <div class="bw-msg">{richText(msg.content)}</div>
              {out && last && (() => {
                // A send that failed shows the error note after it; say so here instead of "Sending".
                const failed = msg.id.startsWith('temp-') && visible.slice(i + 1).some(m => m.id.startsWith('err-'))
                if (failed) {
                  return (
                    <button type="button" class="bw-meta bw-meta--failed bw-retry" onClick={() => retry(msg)}>
                      Not sent. <u>Retry</u>
                    </button>
                  )
                }
                return <div class="bw-meta">{msg.id.startsWith('temp-') ? 'Sending' : timeOf(msg.created_at)}</div>
              })()}
            </div>,
          ]
        })}

        {streamingText && (
          <div class={`bw-row bw-row--in${visible.length && speaker(visible[visible.length - 1]) === 'ai' ? '' : ' bw-row--first'}`}>
            {!(visible.length && speaker(visible[visible.length - 1]) === 'ai') && (
              <div class="bw-who">{brandAvatar}<span><b>Assistant</b></span></div>
            )}
            <div class="bw-msg">{streamingText}</div>
          </div>
        )}

        {aiWaiting && !agentTyping.is_typing && (
          <div class="bw-row bw-row--in bw-row--first">
            <div class="bw-who">{brandAvatar}<span><b>Assistant</b> is typing</span></div>
            {dots}
            <span class="bw-sr">The assistant is typing</span>
          </div>
        )}

        {agentTyping.is_typing && (
          <div class="bw-row bw-row--in bw-row--first">
            <div class="bw-who">
              <span class="bw-ava bw-ava--initials" aria-hidden="true">{agentInitials(agentTyping.name || CONSULTANT_LABEL)}</span>
              <span><b>{agentTyping.name || CONSULTANT_LABEL}</b> is typing</span>
            </div>
            {dots}
          </div>
        )}

        {queued.map((q, i) => (
          <div key={q.id} class={`bw-row bw-row--out bw-row--queued${i === 0 ? ' bw-row--first' : ''}`}>
            <div class="bw-msg">{q.text}</div>
            {i === queued.length - 1 && <div class="bw-meta">Sends when the reply is in</div>}
          </div>
        ))}
      </div>
      {hasUnseen && (
        <button type="button" class="bw-unseen" onClick={() => scrollToEnd(true)}>
          New message <IconArrowDown />
        </button>
      )}
      </div>

      {/* Quick replies — shown after the welcome message, hidden after a tap */}
      {quickReplies && quickReplies.length > 0 && (
        <div class="bw-chips">
          {quickReplies.map((text, i) => (
            <button key={i} type="button" class="bw-chip" onClick={() => handleQuickReply(text)}>
              {text}
            </button>
          ))}
        </div>
      )}

      <div class="bw-compose">
        <textarea
          ref={composerRef}
          id="bw-composer"
          class="bw-input"
          rows={1}
          value={input}
          enterkeyhint="send"
          placeholder="Write a message"
          aria-label="Type a message"
          onInput={e => {
            const val = (e.target as HTMLTextAreaElement).value
            lastInteractionRef.current = Date.now()
            setInput(val)
            sendTypingEvent(val)
            fitComposer()
          }}
          onKeyDown={handleKeyDown}
        />
        <button
          type="button"
          class="bw-send"
          onClick={handleSend}
          disabled={!input.trim()}
          aria-label={sending ? 'Send after the reply' : 'Send message'}
          title={sending ? 'Sends when the reply is in' : 'Send'}
        >
          <IconArrowUp />
        </button>
      </div>
    </div>
  )
}
