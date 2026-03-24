import { useState, useEffect, useRef, useCallback } from 'react'
import { X, Phone, Mail, User, Bot, Headphones, Info, Copy, Check, Send } from 'lucide-react'
import type { Conversation, Message } from '@/lib/types'
import { getConversation, sendAgentMessage } from '@/lib/api'

interface Props { conversationId: string; onClose: () => void; usingMock?: boolean }

// Bubble style per message role
const ROLE_STYLES: Record<string, { bubble: string; align: string; icon: React.ReactNode }> = {
  user:   { bubble: 'bg-[#0B1829] text-white rounded-2xl rounded-br-sm',                             align: 'justify-end',    icon: <User className="w-4 h-4" /> },
  ai:     { bubble: 'bg-[#C9A54E]/10 border border-[#C9A54E]/30 text-gray-800 rounded-2xl rounded-bl-sm', align: 'justify-start', icon: <Bot className="w-4 h-4 text-[#C9A54E]" /> },
  agent:  { bubble: 'bg-gray-100 text-gray-800 rounded-2xl rounded-bl-sm',                           align: 'justify-start',  icon: <Headphones className="w-4 h-4 text-gray-500" /> },
  system: { bubble: 'bg-gray-50 text-gray-500 text-xs italic rounded-lg border border-dashed border-gray-200', align: 'justify-center', icon: <Info className="w-3 h-3" /> },
}

export default function ConversationDetail({ conversationId, onClose, usingMock }: Props) {
  const [conv, setConv]       = useState<Conversation | null>(null)
  const [loading, setLoading] = useState(true)
  const [copied, setCopied]   = useState(false)
  const bottomRef             = useRef<HTMLDivElement>(null)

  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)

  // Reload conversation data (used by polling and after send)
  const reload = useCallback(async () => {
    if (usingMock) return
    try {
      const data = await getConversation(conversationId)
      setConv(data)
    } catch {}
  }, [conversationId, usingMock])

  // Send agent message
  const handleSend = async () => {
    if (!input.trim() || sending) return
    setSending(true)
    try {
      await sendAgentMessage(conversationId, input.trim())
      setInput('')
      await reload()
    } catch (err) {
      console.error('[chat] Failed to send agent message:', err)
    } finally {
      setSending(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    const load = async () => {
      try {
        if (usingMock) throw new Error('mock mode')
        const data = await getConversation(conversationId)
        if (!cancelled) setConv(data)
      } catch (err) {
        console.error('[chat-detail] API error:', err)
        if (!cancelled) setConv(null)
      } finally { if (!cancelled) setLoading(false) }
    }
    load()
    return () => { cancelled = true }
  }, [conversationId, usingMock])

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [conv?.messages?.length])

  // Poll for new messages every 5 seconds (only when conversation is active)
  useEffect(() => {
    if (usingMock || conv?.status === 'closed') return
    const interval = setInterval(reload, 5000)
    return () => clearInterval(interval)
  }, [usingMock, conv?.status, reload])

  const copyId = () => {
    navigator.clipboard.writeText(conversationId)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  if (loading) return <div className="h-full flex items-center justify-center text-gray-400 text-sm">Loading...</div>
  if (!conv) return (
    <div className="h-full flex flex-col items-center justify-center text-gray-400">
      <p className="text-sm">Conversation not found</p>
      <button onClick={onClose} className="mt-2 text-xs text-[#C9A54E] hover:underline">Close</button>
    </div>
  )

  const messages: Message[] = conv.messages ?? []

  return (
    <div className="h-full flex flex-col bg-white">
      {/* Header */}
      <div className="flex items-start justify-between px-5 py-4 bg-[#0B1829]">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="text-base font-semibold text-white truncate">{conv.visitor_name ?? 'Anonymous Visitor'}</h2>
            <span className={`inline-flex px-2 py-0.5 rounded text-[10px] font-medium ${conv.tunnel === 'sales' ? 'bg-blue-500/20 text-blue-200' : 'bg-purple-500/20 text-purple-200'}`}>
              {conv.tunnel}
            </span>
          </div>
          <div className="flex items-center gap-4 mt-1.5">
            {conv.visitor_phone && <span className="flex items-center gap-1 text-xs text-gray-300"><Phone className="w-3 h-3" />{conv.visitor_phone}</span>}
            {conv.visitor_email && <span className="flex items-center gap-1 text-xs text-gray-400"><Mail className="w-3 h-3" />{conv.visitor_email}</span>}
          </div>
          <div className="flex items-center gap-3 mt-1.5 text-[10px] text-gray-400">
            <span>{messages.length} messages</span>
            <span>${conv.ai_cost_total.toFixed(4)} AI cost</span>
            <button onClick={copyId} className="flex items-center gap-0.5 hover:text-gray-200 transition">
              {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
              {conversationId.slice(0, 8)}...
            </button>
          </div>
        </div>
        <button onClick={onClose} className="ml-3 p-1.5 rounded-lg hover:bg-white/10 text-gray-300 hover:text-white transition">
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3 bg-gray-50">
        {messages.length === 0 ? (
          <div className="text-center text-gray-400 text-sm py-8">No messages</div>
        ) : messages.map(msg => {
          const style = ROLE_STYLES[msg.role] ?? ROLE_STYLES.system
          return (
            <div key={msg.id} className={`flex ${style.align} gap-2`}>
              {msg.role !== 'user' && (
                <div className="w-7 h-7 rounded-full bg-white border border-gray-200 flex items-center justify-center shrink-0 mt-1 shadow-sm">
                  {style.icon}
                </div>
              )}
              <div className={`max-w-[75%] px-3.5 py-2.5 shadow-sm ${style.bubble}`}>
                <p className="text-sm leading-relaxed whitespace-pre-wrap">{msg.content}</p>
                <div className="flex items-center justify-end gap-2 mt-1">
                  <span className="text-[10px] opacity-50">
                    {new Date(msg.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </span>
                  {msg.model_used && <span className="text-[10px] opacity-40">{msg.model_used}</span>}
                </div>
              </div>
              {msg.role === 'user' && (
                <div className="w-7 h-7 rounded-full bg-[#C9A54E]/20 border border-[#C9A54E]/30 flex items-center justify-center shrink-0 mt-1">
                  <User className="w-4 h-4 text-[#C9A54E]" />
                </div>
              )}
            </div>
          )
        })}
        <div ref={bottomRef} />
      </div>

      {/* Input + Status Footer */}
      <div className="border-t border-gray-200 bg-white">
        {/* Agent chat input — only show if conversation is active */}
        {conv.status !== 'closed' && (
          <div className="px-4 pt-3 pb-2">
            <div className="flex gap-2">
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Type a reply as agent..."
                rows={1}
                className="flex-1 resize-none rounded-xl border border-gray-200 px-4 py-2.5 text-sm
                           focus:outline-none focus:border-[#C9A54E] focus:ring-1 focus:ring-[#C9A54E]/30
                           placeholder:text-gray-400 disabled:opacity-50"
                disabled={sending}
              />
              <button
                onClick={handleSend}
                disabled={!input.trim() || sending}
                className="flex items-center gap-1.5 px-4 py-2.5 rounded-xl bg-[#0B1829] text-white text-sm font-medium
                           hover:bg-[#0B1829]/90 disabled:opacity-30 disabled:cursor-not-allowed
                           transition-all shrink-0"
              >
                <Send className="w-4 h-4" />
                {sending ? 'Sending...' : 'Send'}
              </button>
            </div>
          </div>
        )}
        {/* Status bar */}
        <div className="px-4 py-2 flex items-center justify-between text-xs text-gray-400 border-t border-gray-50">
          <span>
            Status: <span className={`font-medium ${conv.status === 'active' ? 'text-green-600' : conv.status === 'pending' ? 'text-yellow-600' : 'text-gray-500'}`}>{conv.status}</span>
            {' · '}Mode: <span className={`font-medium ${conv.mode === 'human' ? 'text-blue-600' : conv.mode === 'ai' ? 'text-amber-600' : 'text-gray-600'}`}>{conv.mode}</span>
            {conv.mode === 'ai' && conv.status === 'active' && (
              <span className="ml-2 text-amber-500 text-[10px]">● AI handling</span>
            )}
            {conv.mode === 'human' && (
              <span className="ml-2 text-blue-500 text-[10px]">● You are chatting</span>
            )}
          </span>
          <span>{conv.closed_at ? `Closed ${new Date(conv.closed_at).toLocaleDateString()}` : `Started ${new Date(conv.created_at).toLocaleDateString()}`}</span>
        </div>
      </div>
    </div>
  )
}
