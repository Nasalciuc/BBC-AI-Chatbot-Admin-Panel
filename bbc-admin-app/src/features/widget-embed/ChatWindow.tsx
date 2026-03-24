import { useState, useEffect, useRef } from 'react'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

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
}

export function ChatWindow({ tunnel, visitor, metadata, onClose }: Props) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [convId, setConvId] = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const initialized = useRef(false)

  // Scroll to bottom on new messages
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages.length])

  // Send first greeting automatically
  useEffect(() => {
    if (initialized.current) return
    initialized.current = true
    sendMessage(tunnel === 'sales'
      ? 'Hello, I\'m looking for business class flights.'
      : 'Hello, I need help with my booking.'
    )
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Poll for NEW messages only (incremental) every 3 seconds
  const lastMsgTime = useRef('')

  useEffect(() => {
    if (!convId) return
    const interval = setInterval(async () => {
      if (document.visibilityState !== 'visible') return
      try {
        const afterParam = lastMsgTime.current ? `?after=${encodeURIComponent(lastMsgTime.current)}` : ''
        const res = await fetch(`${API_URL}/api/conversations/${convId}/messages${afterParam}`)
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
  }, [convId])

  const sendMessage = async (text: string) => {
    if (!text.trim()) return
    setSending(true)

    // Optimistic: show user message immediately
    const optimisticMsg: Message = {
      id: `temp-${Date.now()}`,
      role: 'user',
      content: text,
      created_at: new Date().toISOString(),
    }
    setMessages(prev => [...prev, optimisticMsg])

    try {
      const res = await fetch(`${API_URL}/api/chat`, {
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
      }

      // Add AI response (skip if type='queued' — agent will respond via polling)
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

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }

  return (
    <div style={{
      position: 'fixed', bottom: 24, right: 24, width: 380, height: 520,
      background: '#fff', borderRadius: 16,
      boxShadow: '0 8px 30px rgba(0,0,0,0.15)',
      zIndex: 9999, display: 'flex', flexDirection: 'column', overflow: 'hidden',
    }}>
      {/* Header */}
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
        <button onClick={onClose} style={{
          background: 'rgba(255,255,255,0.1)', border: 'none', color: '#fff',
          width: 26, height: 26, borderRadius: '50%', cursor: 'pointer', fontSize: 13,
        }}>✕</button>
      </div>

      {/* Messages */}
      <div style={{ flex: 1, overflowY: 'auto', padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
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
                  ? { background: '#fef3c7', color: '#92400e', fontSize: 12, fontStyle: 'italic' as const }
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

      {/* Input */}
      <div style={{
        borderTop: '1px solid #e5e7eb', padding: 12,
        display: 'flex', gap: 8, flexShrink: 0,
      }}>
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Type a message..."
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
