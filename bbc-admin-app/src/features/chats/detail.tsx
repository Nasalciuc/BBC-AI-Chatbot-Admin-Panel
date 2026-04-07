import { useState, useEffect, useRef, useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  X, Phone, Mail, User, Bot, Headphones, Info, Copy, Check, Send,
  Plane, Calendar, Users, FileText, TrendingUp, Clock,
} from 'lucide-react'
import type { Message, Lead } from '@/lib/types'
import { getConversation, sendAgentMessage, apiFetch } from '@/lib/api'
import { ConfirmDialog } from '@/components/confirm-dialog'

interface Props {
  conversationId: string
  onClose: () => void
  activeTab?: 'my_active' | 'queue' | 'my_closed'
  onConversationChange?: () => void
  usingMock?: boolean
}

const ROLE_STYLES: Record<string, { bubble: string; align: string; icon: React.ReactNode }> = {
  user:   { bubble: 'bg-[#0B1829] text-white rounded-2xl rounded-br-sm',                                      align: 'justify-end',    icon: <User className="w-4 h-4" /> },
  ai:     { bubble: 'bg-[#C9A54E]/10 border border-[#C9A54E]/30 text-gray-800 rounded-2xl rounded-bl-sm',     align: 'justify-start',  icon: <Bot className="w-4 h-4 text-[#C9A54E]" /> },
  agent:  { bubble: 'bg-gray-100 text-gray-800 rounded-2xl rounded-bl-sm',                                     align: 'justify-start',  icon: <Headphones className="w-4 h-4 text-gray-500" /> },
  system: { bubble: 'bg-gray-50 text-gray-500 text-xs italic rounded-lg border border-dashed border-gray-200', align: 'justify-center', icon: <Info className="w-3 h-3" /> },
}

const TIER_COLORS: Record<string, string> = {
  gold:   'bg-yellow-100 text-yellow-800 border-yellow-300',
  silver: 'bg-gray-100 text-gray-700 border-gray-300',
  bronze: 'bg-orange-100 text-orange-800 border-orange-300',
}

export default function ConversationDetail({ conversationId, onClose, activeTab = 'my_active', onConversationChange, usingMock }: Props) {
  const [copied, setCopied]           = useState(false)
  const [closeDialogOpen, setCloseDialogOpen] = useState(false)
  const [input, setInput]             = useState('')
  const [sending, setSending] = useState(false)
  const bottomRef             = useRef<HTMLDivElement>(null)
  const lastMsgTime           = useRef('')
  const queryClient           = useQueryClient()
  const [accumMsgs, setAccumMsgs] = useState<Message[]>([])
  const prevBaseLen           = useRef(0)

  // Full conversation load — cached, long staleTime
  const { data: conv, isLoading: loading } = useQuery({
    queryKey: ['conversation', conversationId],
    queryFn: () => getConversation(conversationId),
    enabled: !usingMock,
    staleTime: 60_000,
  })

  // Track last message timestamp for incremental polling
  useEffect(() => {
    if (conv?.messages?.length) {
      lastMsgTime.current = conv.messages[conv.messages.length - 1].created_at
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conv?.messages?.length])

  // Reset accumulator when base conversation reloads (e.g. after agent sends message)
  useEffect(() => {
    const baseLen = conv?.messages?.length ?? 0
    if (baseLen !== prevBaseLen.current) {
      prevBaseLen.current = baseLen
      setAccumMsgs([])
    }
  }, [conv?.messages?.length])

  // Typing indicator — polls every 1s to show client's live text to agent
  const { data: typingData } = useQuery<{ is_typing: boolean; text: string }>({
    queryKey: ['typing', conversationId],
    queryFn: async () => {
      const res = await apiFetch<{ success: boolean; data: { is_typing: boolean; text: string } }>(
        `/api/conversations/${conversationId}/typing`
      )
      return res.data ?? { is_typing: false, text: '' }
    },
    refetchInterval: activeTab === 'my_active' ? 500 : false,
    enabled: !!conv && activeTab === 'my_active' && conv.status !== 'closed',
  })

  // Incremental message polling — ONLY new messages, ONLY on My Active tab
  const { data: newMessages = [] } = useQuery<Message[]>({
    // eslint-disable-next-line @tanstack/query/exhaustive-deps
    queryKey: ['messages-incremental', conversationId],
    queryFn: async () => {
      if (!lastMsgTime.current) return []
      const res = await apiFetch<{ success: boolean; data: Message[] }>(
        `/api/conversations/${conversationId}/messages?after=${encodeURIComponent(lastMsgTime.current)}`
      )
      if (res.success && res.data?.length > 0) {
        lastMsgTime.current = res.data[res.data.length - 1].created_at
      }
      return res.success ? res.data : []
    },
    refetchInterval: activeTab === 'my_active' ? 2_000 : false,
    enabled: !!conv && activeTab === 'my_active',
  })

  // Accumulate incremental messages — never replace, only append new ones
  useEffect(() => {
    if (!newMessages.length) return
    setAccumMsgs(prev => {
      const existingIds = new Set(prev.map(m => m.id))
      const fresh = newMessages.filter(m => !existingIds.has(m.id))
      return fresh.length ? [...prev, ...fresh] : prev
    })
  }, [newMessages])

  // Merge base messages + accumulated incremental messages (dedup by id)
  const allMessages: Message[] = useMemo(() => {
    const base = conv?.messages ?? []
    if (!accumMsgs.length) return base
    const existingIds = new Set(base.map(m => m.id))
    const fresh = accumMsgs.filter(m => !existingIds.has(m.id))
    return fresh.length > 0 ? [...base, ...fresh] : base
  }, [conv?.messages, accumMsgs])

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [allMessages.length])

  const handleSend = async () => {
    if (!input.trim() || sending) return
    setSending(true)
    try {
      await sendAgentMessage(conversationId, input.trim())
      setInput('')
      queryClient.invalidateQueries({ queryKey: ['conversation', conversationId] })
      queryClient.invalidateQueries({ queryKey: ['messages-incremental', conversationId] })
    } catch (_err) {
      // send failed silently — user can retry
    } finally { setSending(false) }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }

  // Claim conversation from queue
  const handleClaim = async () => {
    try {
      await apiFetch(`/api/conversations/${conversationId}/claim`, { method: 'POST' })
      onConversationChange?.()
    } catch (_err) {
      // claim failed silently
    }
  }

  // Close conversation
  const handleCloseConfirm = async () => {
    try {
      await apiFetch(`/api/conversations/${conversationId}/close`, { method: 'POST' })
      setCloseDialogOpen(false)
      onConversationChange?.()
    } catch (_err) {
      // close failed silently
    }
  }

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

  const lead: Lead | null | undefined = conv.lead

  return (
    <div className="h-full flex bg-white">

      {/* LEFT COLUMN: Chat (header + messages + input) */}
      <div className="flex-1 flex flex-col min-w-0">

        {/* Header */}
        <div className="flex items-start justify-between px-5 py-4 bg-[#0B1829]">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <h2 className="text-base font-semibold text-white truncate">
                {conv.status === 'closed'
                  ? <span className="text-gray-400 italic">Closed conversation</span>
                  : (conv.visitor_name ?? 'Anonymous Visitor')
                }
              </h2>
              <span className={`inline-flex px-2 py-0.5 rounded text-[10px] font-medium ${
                conv.tunnel === 'sales' ? 'bg-blue-500/20 text-blue-200' : 'bg-purple-500/20 text-purple-200'
              }`}>
                {conv.tunnel}
              </span>
            </div>
            <div className="flex items-center gap-4 mt-1.5">
              {conv.visitor_phone && conv.status !== 'closed' && (
                <span className="flex items-center gap-1 text-xs text-gray-300">
                  <Phone className="w-3 h-3" />{conv.visitor_phone}
                </span>
              )}
              {conv.visitor_email && conv.status !== 'closed' && (
                <span className="flex items-center gap-1 text-xs text-gray-400">
                  <Mail className="w-3 h-3" />{conv.visitor_email}
                </span>
              )}
            </div>
            <div className="flex items-center gap-3 mt-1.5 text-[10px] text-gray-400">
              <span>{allMessages.length} messages</span>
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
          {allMessages.length === 0 ? (
            <div className="text-center text-gray-400 text-sm py-8">No messages</div>
          ) : allMessages.map(msg => {
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

        {/* Input + Actions + Status */}
        <div className="border-t border-gray-200 bg-white">
          {/* Typing preview — shown when client is composing a message */}
          {typingData?.is_typing && activeTab === 'my_active' && conv.status !== 'closed' && (
            <div className="mx-4 mb-2 px-3 py-2 bg-blue-50 border border-blue-100 rounded-xl">
              <div className="flex items-center gap-1.5 mb-1">
                <span className="text-[10px] font-medium text-blue-500 uppercase tracking-wide">
                  Client is composing
                </span>
                <span className="flex gap-0.5 items-center">
                  <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                  <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                  <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                </span>
              </div>
              {typingData.text && (
                <p className="text-sm text-gray-600 italic leading-relaxed">
                  &ldquo;{typingData.text}&rdquo;
                </p>
              )}
            </div>
          )}

          {/* Agent input — only on My Active */}
          {activeTab === 'my_active' && conv.status !== 'closed' && (
            <div className="px-4 pt-3 pb-2">
              <div className="flex gap-2">
                <textarea value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={handleKeyDown}
                  placeholder="Type a reply as agent..." rows={1} disabled={sending}
                  className="flex-1 resize-none rounded-xl border border-gray-200 px-4 py-2.5 text-sm focus:outline-none focus:border-[#C9A54E] focus:ring-1 focus:ring-[#C9A54E]/30 placeholder:text-gray-400 disabled:opacity-50" />
                <button onClick={handleSend} disabled={!input.trim() || sending}
                  className="flex items-center gap-1.5 px-4 py-2.5 rounded-xl bg-[#0B1829] text-white text-sm font-medium hover:bg-[#0B1829]/90 disabled:opacity-30 disabled:cursor-not-allowed transition-all shrink-0">
                  <Send className="w-4 h-4" />{sending ? '...' : 'Send'}
                </button>
              </div>
            </div>
          )}

          {/* Take button — only on Queue */}
          {activeTab === 'queue' && (
            <div className="px-4 py-3">
              <button onClick={handleClaim}
                className="w-full py-2.5 rounded-xl bg-[#C9A54E] text-white text-sm font-semibold hover:bg-[#C9A54E]/90 transition-all">
                Take This Conversation
              </button>
            </div>
          )}

          {/* Close button — only on My Active */}
          {activeTab === 'my_active' && conv.status !== 'closed' && (
            <div className="px-4 pb-2">
              <button onClick={() => setCloseDialogOpen(true)}
                className="w-full py-2 rounded-lg border border-gray-200 text-gray-500 text-xs hover:bg-gray-50 hover:text-red-500 transition-all">
                Close Conversation
              </button>
              <ConfirmDialog
                open={closeDialogOpen}
                onOpenChange={setCloseDialogOpen}
                title="Close Conversation"
                desc="Are you sure you want to close this conversation? The visitor will no longer receive replies."
                confirmText="Close"
                destructive
                handleConfirm={handleCloseConfirm}
              />
            </div>
          )}

          {/* Status bar */}
          <div className="px-4 py-2 flex items-center justify-between text-xs text-gray-400 border-t border-gray-50">
            <span>
              Status: <span className={`font-medium ${conv.status === 'active' ? 'text-green-600' : conv.status === 'pending' ? 'text-yellow-600' : 'text-gray-500'}`}>{conv.status}</span>
              {' · '}Mode: <span className={`font-medium ${conv.mode === 'human' ? 'text-blue-600' : conv.mode === 'ai' ? 'text-amber-600' : 'text-gray-600'}`}>{conv.mode}</span>
              {conv.mode === 'ai' && conv.status === 'active' && <span className="ml-2 text-amber-500 text-[10px]">● AI handling</span>}
              {conv.mode === 'human' && conv.status === 'active' && <span className="ml-2 text-blue-500 text-[10px]">● You are chatting</span>}
            </span>
            <span>{conv.closed_at ? `Closed ${new Date(conv.closed_at).toLocaleDateString()}` : `Started ${new Date(conv.created_at).toLocaleDateString()}`}</span>
          </div>
        </div>
      </div>

      {/* RIGHT COLUMN: Lead Info Panel (272px, hidden on mobile, hidden on My Closed) */}
      {activeTab !== 'my_closed' && (
      <div className="w-72 border-l border-gray-200 bg-gray-50 overflow-y-auto shrink-0 hidden lg:block">
        <div className="p-4 space-y-4">

          {/* AI Summary Card */}
          {conv.summary && (
            <div className="bg-white rounded-xl p-3 border border-gray-100 shadow-sm">
              <div className="flex items-center gap-1.5 text-xs font-medium text-gray-500 mb-2">
                <FileText className="w-3.5 h-3.5" />
                AI Summary
              </div>
              <p className="text-sm text-gray-700 leading-relaxed">{conv.summary}</p>
            </div>
          )}

          {/* Lead Score Card */}
          {lead && (
            <div className="bg-white rounded-xl p-3 border border-gray-100 shadow-sm">
              <div className="flex items-center justify-between mb-2">
                <span className="flex items-center gap-1.5 text-xs font-medium text-gray-500">
                  <TrendingUp className="w-3.5 h-3.5" />
                  Lead Score
                </span>
                <span className={`px-2 py-0.5 rounded-full text-xs font-semibold border ${TIER_COLORS[lead.tier] ?? TIER_COLORS.bronze}`}>
                  {lead.tier.toUpperCase()}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <div className="flex-1 h-2 bg-gray-100 rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${
                      lead.score >= 80 ? 'bg-yellow-500' : lead.score >= 50 ? 'bg-gray-400' : 'bg-orange-400'
                    }`}
                    style={{ width: `${lead.score}%` }}
                  />
                </div>
                <span className="text-sm font-bold text-gray-700">{lead.score}</span>
              </div>
              <p className="text-[10px] text-gray-400 mt-1">
                Status: <span className="font-medium text-gray-600">{lead.status}</span>
              </p>
            </div>
          )}

          {/* Route Card */}
          {lead && (lead.origin_code || lead.destination_code) && (
            <div className="bg-white rounded-xl p-3 border border-gray-100 shadow-sm">
              <div className="flex items-center gap-1.5 text-xs font-medium text-gray-500 mb-2">
                <Plane className="w-3.5 h-3.5" />
                Route
              </div>
              {lead.route_display ? (
                <p className="text-sm font-semibold text-gray-800">{lead.route_display}</p>
              ) : (
                <p className="text-sm text-gray-700">{lead.origin_code ?? '?'} → {lead.destination_code ?? '?'}</p>
              )}
              <div className="mt-2 space-y-1 text-xs text-gray-500">
                {lead.trip_type && lead.trip_type !== 'round_trip' && (
                  <p>Type: <span className="text-gray-700">{lead.trip_type.replace(/_/g, ' ')}</span></p>
                )}
                <p>Class: <span className="text-gray-700 capitalize">{lead.cabin_class}</span></p>
              </div>
            </div>
          )}

          {/* Dates Card */}
          {lead && (lead.departure_date || lead.return_date) && (
            <div className="bg-white rounded-xl p-3 border border-gray-100 shadow-sm">
              <div className="flex items-center gap-1.5 text-xs font-medium text-gray-500 mb-2">
                <Calendar className="w-3.5 h-3.5" />
                Dates
              </div>
              <div className="space-y-1 text-sm text-gray-700">
                {lead.departure_date && (
                  <p>Depart: <span className="font-medium">
                    {new Date(lead.departure_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                  </span></p>
                )}
                {lead.return_date && (
                  <p>Return: <span className="font-medium">
                    {new Date(lead.return_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })}
                  </span></p>
                )}
                {lead.flexible_dates && (
                  <p className="text-xs text-green-600">Flexible dates</p>
                )}
              </div>
            </div>
          )}

          {/* Passengers Card */}
          {lead?.passengers && (
            <div className="bg-white rounded-xl p-3 border border-gray-100 shadow-sm">
              <div className="flex items-center gap-1.5 text-xs font-medium text-gray-500 mb-2">
                <Users className="w-3.5 h-3.5" />
                Passengers
              </div>
              <p className="text-sm font-medium text-gray-700">
                {lead.passengers} {lead.passengers === 1 ? 'traveler' : 'travelers'}
              </p>
            </div>
          )}

          {/* Contact Card */}
          <div className="bg-white rounded-xl p-3 border border-gray-100 shadow-sm">
            <div className="flex items-center gap-1.5 text-xs font-medium text-gray-500 mb-2">
              <User className="w-3.5 h-3.5" />
              Contact
            </div>
            <div className="space-y-1.5 text-sm">
              {conv.visitor_name && (
                <p className="flex items-center gap-2 text-gray-700">
                  <User className="w-3 h-3 text-gray-400 shrink-0" />
                  {conv.visitor_name}
                </p>
              )}
              {conv.visitor_phone && (
                <p className="flex items-center gap-2 text-gray-700">
                  <Phone className="w-3 h-3 text-gray-400 shrink-0" />
                  <a href={`tel:${conv.visitor_phone}`} className="text-[#C9A54E] hover:underline">
                    {conv.visitor_phone}
                  </a>
                </p>
              )}
              {conv.visitor_email && (
                <p className="flex items-center gap-2 text-gray-700">
                  <Mail className="w-3 h-3 text-gray-400 shrink-0" />
                  <a href={`mailto:${conv.visitor_email}`} className="text-[#C9A54E] hover:underline text-xs break-all">
                    {conv.visitor_email}
                  </a>
                </p>
              )}
              {!conv.visitor_name && !conv.visitor_phone && !conv.visitor_email && (
                <p className="text-xs text-gray-400 italic">No contact info yet</p>
              )}
            </div>
          </div>

          {/* Notes Card */}
          {lead?.notes && lead.notes.trim() !== '' && (
            <div className="bg-white rounded-xl p-3 border border-gray-100 shadow-sm">
              <div className="flex items-center gap-1.5 text-xs font-medium text-gray-500 mb-2">
                <FileText className="w-3.5 h-3.5" />
                Notes
              </div>
              <p className="text-sm text-gray-600 whitespace-pre-wrap">{lead.notes}</p>
            </div>
          )}

          {/* Conversation Meta Card */}
          <div className="bg-white rounded-xl p-3 border border-gray-100 shadow-sm">
            <div className="flex items-center gap-1.5 text-xs font-medium text-gray-500 mb-2">
              <Clock className="w-3.5 h-3.5" />
              Details
            </div>
            <div className="space-y-1 text-xs text-gray-500">
              <p>Started: <span className="text-gray-700">{new Date(conv.created_at).toLocaleString()}</span></p>
              <p>Messages: <span className="text-gray-700">{allMessages.length}</span></p>
              <p>AI cost: <span className="text-gray-700">${conv.ai_cost_total.toFixed(4)}</span></p>
              {conv.assigned_agent_id && (
                <p>Agent: <span className="text-gray-700">{conv.assigned_agent_id.slice(0, 8)}...</span></p>
              )}
            </div>
          </div>

          {/* No Lead placeholder */}
          {!lead && (
            <div className="bg-white rounded-xl p-3 border border-dashed border-gray-200">
              <p className="text-xs text-gray-400 text-center italic">No lead data captured yet</p>
            </div>
          )}

        </div>
      </div>
      )}

    </div>
  )
}
