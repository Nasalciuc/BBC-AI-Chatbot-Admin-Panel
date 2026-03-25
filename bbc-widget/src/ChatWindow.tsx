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

// Safe sessionStorage (may be disabled)
function safeGet(key: string): string | null {
  try { return sessionStorage.getItem(key) } catch { return null }
}
function safeSet(key: string, val: string): void {
  try { sessionStorage.setItem(key, val) } catch {}
}

export function ChatWindow({ tunnel, visitor, metadata, onClose, apiUrl }: Props) {
  const savedConvId = safeGet('bbc_conv_id')

  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [convId, setConvId] = useState<string | null>(savedConvId)
  const bottomRef = useRef<HTMLDivElement>(null)
  const initialized = useRef(false)

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

  // Poll for new messages (incremental)
  const lastMsgTime = useRef('')

  useEffect(() => {
    if (!convId) return
    const interval = setInterval(async () => {
      if (document.visibilityState !== 'visible') return
      try {
        const afterParam = lastMsgTime.current ? `?after=${encodeURIComponent(lastMsgTime.current)}` : ''
        const res = await fetch(`${apiUrl}/api/conversations/${convId}/messages${afterParam}`)
        if (!res.ok) return
        const json = await res.json()
        if (json.success && json.data && json.data.length > 0) {
          setMessages(prev => {
            const existingIds = new Set(prev.map(m => m.id))
            const newMsgs = (json.data as Message[]).filter(m => !existingIds.has(m.id))
            if (newMsgs.length === 0) return prev
            return [...prev, ...newMsgs]
          })
          lastMsgTime.current = json.data[json.data.length - 1].created_at
        }
      } catch { /* polling failure is non-fatal */ }
    }, 3000)
    return () => clearInterval(interval)
  }, [convId, apiUrl])

  const sendMessage = async (text: string) => {
    if (!text.trim()) return
    setSending(true)

    const optimisticMsg: Message = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    }
    setMessages(prev => [...prev, optimisticMsg])

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
      }

      if (data.message && data.type !== 'queued') {
        const aiMsg: Message = {
          id: `ai-${Date.now()}`,
          role: 'ai',
          content: data.message,
          created_at: new Date().toISOString(),
        }
        setMessages(prev => [...prev, aiMsg])
        lastMsgTime.current = aiMsg.created_at
      }
    } catch {
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

      <div style={{
        borderTop: '1px solid #e5e7eb', padding: 12,
        display: 'flex', gap: 8, flexShrink: 0,
      }}>
        <input
          value={input}
          onInput={e => setInput((e.target as HTMLInputElement).value)}
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
