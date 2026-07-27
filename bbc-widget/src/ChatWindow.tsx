import { useState, useEffect, useRef } from 'preact/hooks'
import { apiFetch, getVisitorId } from './api'
import brand from './config'

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
  const bottomRef = useRef<HTMLDivElement>(null)
  const initialized = useRef(false)
  const typingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastTypingSentRef = useRef<number>(0)
  const closeSentRef = useRef(false)
  /** Operator-typing state, polled from the backend (name only, never the draft). */
  const [agentTyping, setAgentTyping] = useState<{ is_typing: boolean; name: string }>({
    is_typing: false,
    name: '',
  })
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

  // Scroll to bottom on new messages
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages.length, streamingText, sending])

  // Send first greeting — SKIP if restored session
  useEffect(() => {
    if (initialized.current) return
    initialized.current = true
    if (savedConvId) return
    sendMessage(tunnel === 'sales'
      ? 'Hello, I\'m looking for business class flights.'
      : 'Hello, I need help with my booking.'
    )
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

  // Send greeting after closed conversation was detected and cleared
  useEffect(() => {
    if (!pendingGreeting || convId || sending) return
    setPendingGreeting(false)
    sendMessage(tunnel === 'sales'
      ? 'Hello, I\'m looking for business class flights.'
      : 'Hello, I need help with my booking.'
    )
  }, [pendingGreeting, convId, sending])

  // Real-time delivery: SSE primary + polling fallback (RxDB checkpoint pattern)
  const lastMsgTime = useRef('')

  useEffect(() => {
    if (!convId) return
    closeSentRef.current = false

    const handlePageLeave = () => notifySessionClose('left', true)
    window.addEventListener('pagehide', handlePageLeave)
    window.addEventListener('beforeunload', handlePageLeave)

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
            // Remove optimistic temp-* messages — real versions arrived from server
            const cleaned = prev.filter(m => !m.id.startsWith('temp-'))
            const ids = new Set(cleaned.map(m => m.id))
            const newMsgs = incoming.filter(m => !ids.has(m.id))
            if (newMsgs.length === 0) {
              return cleaned.length !== prev.length ? cleaned : prev
            }
            if (newMsgs.some(m => m.role === 'ai')) {
              setIsStreaming(false)
              isStreamingRef.current = false
              setSending(false)
            }
            return [...cleaned, ...newMsgs]
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

          const msg = parsed as Message
          setMessages(prev => {
            if (prev.some(m => m.id === msg.id)) return prev
            return [...prev, msg]
          })
          if (msg.created_at) lastMsgTime.current = msg.created_at
        } catch { /* malformed event — ignore */ }
      }

      source.onerror = () => {
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
      window.removeEventListener('pagehide', handlePageLeave)
      window.removeEventListener('beforeunload', handlePageLeave)
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
    if (!input.trim() || sending) return
    sendMessage(input.trim())
    setInput('')
  }

  const handleQuickReply = (text: string) => {
    setQuickReplies(null)
    sendMessage(text)
  }

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }

  return (
    <div style={embedded ? {
      // CRM iframe mode: fill the container — no floating-card chrome.
      width: '100%', height: '100%',
      background: '#fff',
      display: 'flex', flexDirection: 'column', overflow: 'hidden',
    } : {
      position: 'fixed', bottom: 24, right: 24, width: 380, height: 520,
      maxWidth: 'calc(100vw - 16px)', maxHeight: 'calc(100dvh - 16px)',
      background: '#fff', borderRadius: 16,
      boxShadow: '0 8px 30px rgba(0,0,0,0.15)',
      zIndex: 2147483000, display: 'flex', flexDirection: 'column', overflow: 'hidden',
    }}>
      <style>{`
        @keyframes bbcDot {
          0%, 80%, 100% { opacity: 0.3; transform: scale(0.8); }
          40% { opacity: 1; transform: scale(1); }
        }
      `}</style>
      <div style={{
        background: 'var(--bbc-header)', color: 'var(--bbc-header-text)', padding: '14px 18px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <img
            src={brand.logoUrl}
            alt={brand.logoAlt}
            width={24}
            height={24}
            style={{ borderRadius: 5, objectFit: 'contain' }}
          />
          <div>
            <div style={{ fontWeight: 600, fontSize: 14 }}>{brand.chatTitle}</div>
            <div style={{ fontSize: 11, opacity: 0.6, marginTop: 1 }}>
              {visitor.name ? `Hi ${visitor.name}!` : tunnel === 'sales' ? brand.chatSubtitle : 'Booking Support'}
            </div>
          </div>
        </div>
        {!embedded && (
          <button onClick={() => { notifySessionClose('minimized', true); onClose() }} aria-label="Close chat" style={{
            background: 'rgba(255,255,255,0.1)', border: 'none', color: 'var(--bbc-header-text)',
            width: 26, height: 26, borderRadius: '50%', cursor: 'pointer', fontSize: 13,
          }}>✕</button>
        )}
      </div>

      <div role="log" aria-live="polite" style={{ flex: 1, overflowY: 'auto', padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
        {messages.filter(m => m.role !== 'system' || m.content).map(msg => {
          const isOperator = msg.role === 'agent'
          const operatorName = (msg.agent_name || '').trim() || CONSULTANT_LABEL
          return (
          <div key={msg.id} style={{
            alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start',
            maxWidth: '80%',
          }}>
            {isOperator && (
              <div style={{ fontSize: 11, marginBottom: 3, marginLeft: 34, lineHeight: 1.3 }}>
                <span style={{ fontWeight: 600, color: 'var(--bbc-header)' }}>{operatorName}</span>
                <span style={{ color: '#9ca3af' }}> · {CONSULTANT_LABEL}</span>
              </div>
            )}
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 6 }}>
              {isOperator && (
                msg.agent_avatar_url && !brokenAvatars[msg.id]
                  ? <img
                      src={msg.agent_avatar_url}
                      alt={operatorName}
                      style={{
                        width: 28, height: 28, borderRadius: '50%', objectFit: 'cover',
                        flexShrink: 0, background: '#e5e7eb',
                      }}
                      onError={() => setBrokenAvatars(prev => ({ ...prev, [msg.id]: true }))}
                    />
                  : <div style={{
                      width: 28, height: 28, borderRadius: '50%', flexShrink: 0,
                      background: 'var(--bbc-header)', color: 'var(--bbc-header-text)',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      fontSize: 11, fontWeight: 600,
                    }}>
                      {agentInitials(operatorName)}
                    </div>
              )}
              <div style={{
                padding: '10px 14px', borderRadius: 14, fontSize: 13, lineHeight: 1.5,
                ...(msg.role === 'user'
                  ? { background: 'var(--bbc-user-bubble)', color: 'var(--bbc-header-text)', borderBottomRightRadius: 4 }
                  : msg.role === 'system'
                    ? { background: '#fef3c7', color: '#92400e', fontSize: 12, fontStyle: 'italic' }
                    : { background: 'var(--bbc-ai-bubble)', color: 'var(--bbc-ai-bubble-text)', borderBottomLeftRadius: 4 }
                ),
              }}>
                {msg.content}
              </div>
            </div>
            <div style={{
              fontSize: 10, color: '#9ca3af', marginTop: 2,
              textAlign: msg.role === 'user' ? 'right' : 'left',
              marginLeft: isOperator ? 34 : 0,
            }}>
              {new Date(msg.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </div>
          </div>
          )
        })}
        {agentTyping.is_typing && (
          <div style={{ alignSelf: 'flex-start', maxWidth: '80%' }}>
            <div style={{ fontSize: 11, marginBottom: 3, color: '#9ca3af', lineHeight: 1.3 }}>
              {(agentTyping.name || CONSULTANT_LABEL)} is typing…
            </div>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 4,
              padding: '12px 16px', background: 'var(--bbc-ai-bubble)',
              borderRadius: 14, borderBottomLeftRadius: 4, width: 'fit-content',
            }}>
              {[0, 1, 2].map(i => (
                <span
                  key={i}
                  ref={el => { if (el) {
                    el.style.animation = 'none'
                    el.offsetHeight // reflow
                    el.style.animation = `bbcDot 1.4s ${i * 0.2}s infinite`
                  }}}
                  style={{
                    width: 6, height: 6, borderRadius: '50%',
                    background: '#999', display: 'inline-block',
                  }}
                />
              ))}
            </div>
          </div>
        )}
        {sending && !streamingText && !isStreaming && (
          <div style={{ display: 'flex', justifyContent: 'flex-start', marginBottom: 4, paddingLeft: 8 }}>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              padding: '12px 16px',
              background: '#f0f0f0',
              borderRadius: 12,
              width: 'fit-content',
            }}>
              {[0, 1, 2].map(i => (
                <span
                  key={i}
                  ref={el => { if (el) {
                    el.style.animation = 'none'
                    el.offsetHeight // reflow
                    el.style.animation = `bbcDot 1.4s ${i * 0.2}s infinite`
                  }}}
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: '50%',
                    background: '#999',
                    display: 'inline-block',
                  }}
                />
              ))}
            </div>
          </div>
        )}
        {streamingText && (
          <div style={{ alignSelf: 'flex-start', maxWidth: '80%' }}>
            <div style={{
              padding: '10px 14px', borderRadius: 14, fontSize: 13, lineHeight: 1.5,
              background: 'var(--bbc-ai-bubble)', color: 'var(--bbc-ai-bubble-text)', borderBottomLeftRadius: 4,
            }}>
              {streamingText}
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* Quick reply buttons — shown after welcome message, hidden after click */}
      {quickReplies && quickReplies.length > 0 && (
        <div style={{
          padding: '8px 14px',
          display: 'flex',
          flexWrap: 'wrap',
          gap: 6,
          borderTop: '1px solid #f3f4f6',
        }}>
          {quickReplies.map((text, i) => (
            <button
              key={i}
              onClick={() => handleQuickReply(text)}
              style={{
                padding: '6px 12px',
                borderRadius: 16,
                fontSize: 12,
                border: '1.5px solid var(--bbc-primary)',
                background: '#fff',
                color: 'var(--bbc-header)',
                cursor: 'pointer',
                fontWeight: 500,
              }}
            >
              {text}
            </button>
          ))}
        </div>
      )}

      <div style={{
        borderTop: '1px solid #e5e7eb', padding: 12,
        display: 'flex', gap: 8, flexShrink: 0,
      }}>
        <input
          value={input}
          onInput={e => {
            const val = (e.target as HTMLInputElement).value
            setInput(val)
            sendTypingEvent(val)
          }}
          onKeyDown={handleKeyDown}
          placeholder="Type a message..."
          aria-label="Type a message"
          disabled={sending}
          style={{
            flex: 1, padding: '10px 14px', borderRadius: 10,
            border: '1px solid #e5e7eb', fontSize: 13, outline: 'none',
          }}
        />
        <button
          onClick={handleSend}
          disabled={!input.trim() || sending}
          style={{
            padding: '10px 16px', borderRadius: 10,
            background: !input.trim() || sending ? '#d1d5db' : 'var(--bbc-send-btn)',
            color: 'var(--bbc-send-btn-text)', border: 'none', cursor: !input.trim() || sending ? 'default' : 'pointer',
            fontWeight: 600, fontSize: 13, flexShrink: 0,
          }}
        >
          Send
        </button>
      </div>
    </div>
  )
}
