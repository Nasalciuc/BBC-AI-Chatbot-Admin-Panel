import { useState, useEffect, useRef } from 'preact/hooks'

interface Message {
  id: string
  role: 'user' | 'ai' | 'agent' | 'system'
  content: string
  created_at: string
}

interface Props {
  tunnel: 'sales' | 'support'
  visitor: { name?: string; email?: string; phone?: string }
  metadata?: { booking_id?: string }
  onClose: () => void
  apiUrl: string
}

// Safe localStorage (may be disabled)
function safeGet(key: string): string | null {
  try { return localStorage.getItem(key) } catch { return null }
}
function safeSet(key: string, val: string): void {
  try { localStorage.setItem(key, val) } catch {}
}

const SESSION_TTL_MS = 30 * 60 * 1000  // 30 minutes — must match Widget.tsx

/** Validates stored conv_id against expiry and visitor fingerprint.
 *  Returns the conv_id if valid, null if expired or visitor mismatch. */
function getValidConvId(visitor: Props['visitor']): string | null {
  const convId = safeGet('bbc_conv_id')
  if (!convId) return null

  // Check 30-minute expiry (rolling — updated on each message)
  const ts = safeGet('bbc_conv_ts')
  if (!ts || Date.now() - parseInt(ts) > SESSION_TTL_MS) {
    try { localStorage.removeItem('bbc_conv_id') } catch {}
    try { localStorage.removeItem('bbc_conv_ts') } catch {}
    try { localStorage.removeItem('bbc_visitor_key') } catch {}
    return null
  }

  // Check visitor fingerprint match
  const savedKey = safeGet('bbc_visitor_key')
  const currentKey = `${visitor.name || ''}|${visitor.email || ''}|${visitor.phone || ''}`
  if (savedKey && currentKey && savedKey !== currentKey) {
    try { localStorage.removeItem('bbc_conv_id') } catch {}
    try { localStorage.removeItem('bbc_conv_ts') } catch {}
    try { localStorage.removeItem('bbc_visitor_key') } catch {}
    return null
  }

  return convId
}

export function ChatWindow({ tunnel, visitor, metadata, onClose, apiUrl }: Props) {
  const savedConvId = getValidConvId(visitor)

  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [quickReplies, setQuickReplies] = useState<string[] | null>(null)
  const [pendingGreeting, setPendingGreeting] = useState(false)
  const [convId, setConvId] = useState<string | null>(savedConvId)
  const bottomRef = useRef<HTMLDivElement>(null)
  const initialized = useRef(false)
  const typingTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastTypingSentRef = useRef<number>(0)

  // Scroll to bottom on new messages
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages.length])

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
        const res = await fetch(`${apiUrl}/api/chat/messages/${convId}${after}`)
        if (!res.ok) return
        const json = await res.json()
        if (json.success && json.data && json.data.length > 0) {
          setMessages(prev => {
            const ids = new Set(prev.map(m => m.id))
            const newMsgs = (json.data as Message[]).filter(m => !ids.has(m.id))
            if (newMsgs.length === 0) return prev
            return [...prev, ...newMsgs]
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
      source = new EventSource(`${apiUrl}/api/chat/stream/${convId}`)

      source.onopen = () => {
        errorCount = 0        // reset error counter on successful connect
        catchUpPoll()         // sync messages missed during connect/reconnect
      }

      source.onmessage = (e: MessageEvent) => {
        try {
          const msg = JSON.parse(e.data) as Message
          setMessages(prev => {
            if (prev.some(m => m.id === msg.id)) return prev  // dedup
            return [...prev, msg]
          })
          lastMsgTime.current = msg.created_at
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

    startSSE()

    return () => {
      source?.close()
      if (fallbackInterval) clearInterval(fallbackInterval)
      if (typingTimeoutRef.current) clearTimeout(typingTimeoutRef.current)
    }
  }, [convId, apiUrl])

  // Send typing event to server
  // Throttle: send at most every 200ms so operator sees text live
  // Clear debounce: if no typing for 3s → clear indicator
  const sendTypingEvent = (text: string) => {
    if (!convId) return

    const now = Date.now()

    // Throttle — send immediately if 200ms passed since last send
    if (text.trim() && now - lastTypingSentRef.current > 200) {
      lastTypingSentRef.current = now
      fetch(`${apiUrl}/api/chat/typing/${convId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text }),
      }).catch(() => {})
    }

    // Debounce clear — if user stops typing for 3s → clear indicator
    if (typingTimeoutRef.current) clearTimeout(typingTimeoutRef.current)
    typingTimeoutRef.current = setTimeout(() => {
      fetch(`${apiUrl}/api/chat/typing/${convId}`, {
        method: 'DELETE',
      }).catch(() => {})
    }, 3_000)
  }

  const sendMessage = async (text: string) => {
    if (!text.trim()) return
    // Clear typing indicator immediately when sending
    if (convId) {
      if (typingTimeoutRef.current) clearTimeout(typingTimeoutRef.current)
      fetch(`${apiUrl}/api/chat/typing/${convId}`, { method: 'DELETE' }).catch(() => {})
    }
    setSending(true)

    const optimisticMsg: Message = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    }
    setMessages(prev => [...prev, optimisticMsg])

    // Show "Connecting…" after user message — only on first message
    if (!convId && !sending) {
      setMessages(prev => [...prev, {
        id: '__connecting_temp__',
        role: 'system' as const,
        content: 'Connecting you with a specialist now\u2026',
        created_at: new Date().toISOString(),
      }])
    }

    try {
      const res = await fetch(`${apiUrl}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: text,
          conversation_id: convId,
          tunnel,
          visitor: {
            name: visitor.name || null,
            email: visitor.email || null,
            phone: visitor.phone || null,
          },
          metadata: metadata || {},
        }),
      })

      if (!res.ok) throw new Error('API error')
      const data = await res.json()

      if (data.conversation_id && !convId) {
        setConvId(data.conversation_id)
        safeSet('bbc_conv_id', data.conversation_id)
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
      // Clean up optimistic message if the request failed
      setMessages(prev => prev.filter(m => m.id !== '__connecting_temp__'))
      const errMsg: Message = {
        id: `err-${Date.now()}`,
        role: 'system',
        content: 'Connection error. Please try again.',
        created_at: new Date().toISOString(),
      }
      setMessages(prev => [...prev, errMsg])
    } finally {
      setSending(false)
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
    <div style={{
      position: 'fixed', bottom: 24, right: 24, width: 380, height: 520,
      maxWidth: 'calc(100vw - 16px)', maxHeight: 'calc(100dvh - 16px)',
      background: '#fff', borderRadius: 16,
      boxShadow: '0 8px 30px rgba(0,0,0,0.15)',
      zIndex: 2147483000, display: 'flex', flexDirection: 'column', overflow: 'hidden',
    }}>
      <div style={{
        background: '#0B1829', color: '#fff', padding: '14px 18px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexShrink: 0,
      }}>
        <div>
          <div style={{ fontWeight: 600, fontSize: 14 }}>BBC Travel Concierge</div>
          <div style={{ fontSize: 11, opacity: 0.6, marginTop: 1 }}>
            {visitor.name ? `Hi ${visitor.name}!` : tunnel === 'sales' ? 'Business Class Experts' : 'Booking Support'}
          </div>
        </div>
        <button onClick={onClose} aria-label="Close chat" style={{
          background: 'rgba(255,255,255,0.1)', border: 'none', color: '#fff',
          width: 26, height: 26, borderRadius: '50%', cursor: 'pointer', fontSize: 13,
        }}>✕</button>
      </div>

      <div role="log" aria-live="polite" style={{ flex: 1, overflowY: 'auto', padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
        {messages.filter(m => m.role !== 'system' || m.content).map(msg => (
          <div key={msg.id} style={{
            alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start',
            maxWidth: '80%',
          }}>
            <div style={{
              padding: '10px 14px', borderRadius: 14, fontSize: 13, lineHeight: 1.5,
              ...(msg.role === 'user'
                ? { background: '#0B1829', color: '#fff', borderBottomRightRadius: 4 }
                : msg.role === 'system'
                  ? { background: '#fef3c7', color: '#92400e', fontSize: 12, fontStyle: 'italic' }
                  : { background: '#f3f4f6', color: '#1f2937', borderBottomLeftRadius: 4 }
              ),
            }}>
              {msg.content}
            </div>
            <div style={{
              fontSize: 10, color: '#9ca3af', marginTop: 2,
              textAlign: msg.role === 'user' ? 'right' : 'left',
            }}>
              {new Date(msg.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </div>
          </div>
        ))}
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
                border: '1.5px solid #C9A54E',
                background: '#fff',
                color: '#0B1829',
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
            background: !input.trim() || sending ? '#d1d5db' : '#C9A54E',
            color: '#fff', border: 'none', cursor: !input.trim() || sending ? 'default' : 'pointer',
            fontWeight: 600, fontSize: 13, flexShrink: 0,
          }}
        >
          Send
        </button>
      </div>
    </div>
  )
}
