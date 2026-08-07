import { useState, useEffect, useRef, useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  X, Phone, Mail, User, Bot, Headphones, Info, Copy, Check, Send, Smile,
  Plane, Calendar, Users, FileText, TrendingUp, Clock, Globe,
} from 'lucide-react'
import type { Message, Lead } from '@/lib/types'
import {
  getConversation,
  sendAgentMessage,
  apiFetch,
  blockConversationVisitor,
  postAgentTyping,
  clearAgentTyping,
} from '@/lib/api'
import type { ApiError } from '@/lib/api'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { ReassignPanel } from '@/components/reassign-panel'
import { usePermissions } from '@/lib/bbc/hooks'
import type { UserRole } from '@/lib/bbc/types'
import { useAuthStore } from '@/stores/auth-store'
import { OperatorHistory, OperatorBadge } from './operator-history'
import { describeClientPresence, presenceFromMetadata } from './presence'

interface Props {
  conversationId: string
  onClose: () => void
  activeTab?: 'my_active' | 'my_closed' | 'all_active' | 'all_closed'
  onConversationChange?: () => void
  usingMock?: boolean
}

const DETAIL_TAG_LABELS: Record<string, string> = {
  fresh: 'Fresh',
  active: 'Active',
  main_queue: 'Main Queue',
  completed: 'Completed',
  abandoned: 'Abandoned',
  no_engagement: 'No engagement',
}

const ROLE_STYLES: Record<string, { bubble: string; align: string; icon: React.ReactNode }> = {
  user:   { bubble: 'bg-[#0B1829] text-white rounded-2xl rounded-br-sm',                                      align: 'justify-end',    icon: <User className="w-4 h-4" /> },
  ai:     { bubble: 'bg-[#C9A54E]/10 border border-[#C9A54E]/30 text-foreground rounded-2xl rounded-bl-sm',     align: 'justify-start',  icon: <Bot className="w-4 h-4 text-[#C9A54E]" /> },
  agent:  { bubble: 'bg-muted text-foreground rounded-2xl rounded-bl-sm',                                     align: 'justify-start',  icon: <Headphones className="w-4 h-4 text-muted-foreground" /> },
  system: { bubble: 'bg-muted text-muted-foreground text-xs italic rounded-lg border border-dashed border-border', align: 'justify-center', icon: <Info className="w-3 h-3" /> },
}

const TIER_COLORS: Record<string, string> = {
  gold:   'bg-yellow-100 text-yellow-800 border-yellow-300',
  silver: 'bg-muted text-muted-foreground border-border',
  bronze: 'bg-orange-100 text-orange-800 border-orange-300',
}

export default function ConversationDetail({ conversationId, onClose, activeTab = 'my_active', onConversationChange, usingMock }: Props) {
  const role = (useAuthStore((s) => s.auth.user?.role ?? 'sales') as UserRole)
  const isAdmin = ['owner', 'admin', 'dev'].includes(role)
  const canViewHistory = ['owner', 'admin', 'dev', 'supervisor', 'qa'].includes(role)
  const permissions = usePermissions(role)
  const canBlockVisitor = ['owner', 'admin', 'dev', 'supervisor', 'project_manager'].includes(role)
  const [copied, setCopied]           = useState(false)
  const [closeDialogOpen, setCloseDialogOpen] = useState(false)
  const [blockDialogOpen, setBlockDialogOpen] = useState(false)
  const [blocking, setBlocking] = useState(false)
  const [blockResult, setBlockResult] = useState<string | null>(null)
  const [input, setInput]             = useState('')
  const [showEmojiPicker, setShowEmojiPicker] = useState(false)
  const [sending, setSending] = useState(false)
  const [markingLead, setMarkingLead] = useState(false)
  const [markLeadError, setMarkLeadError] = useState<string | null>(null)
  const bottomRef             = useRef<HTMLDivElement>(null)
  const taRef                 = useRef<HTMLTextAreaElement>(null)
  const lastTypingSentRef     = useRef<number>(0)
  const MAX_TA_ROWS           = 6
  const lastMsgTime           = useRef('')
  const queryClient           = useQueryClient()
  const [accumMsgs, setAccumMsgs] = useState<Message[]>([])
  const prevBaseLen           = useRef(0)

  // Full conversation load — cached, long staleTime
  const { data: conv, isLoading: loading, isError, error, refetch } = useQuery({
    queryKey: ['conversation', conversationId],
    queryFn: () => getConversation(conversationId),
    enabled: !usingMock,
    // Bug 5: do NOT cache null/error — otherwise a single transient Supabase
    // failure poisons the UI with "not found" for 60 seconds. staleTime=0
    // means every click refetches; combined with retry, a genuine flake
    // is recovered within ~1 second.
    staleTime: 0,
    retry: 1,
    retryDelay: 500,
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

  // Presence polling — lightweight metadata endpoint for real-time client status.
  const { data: presenceData } = useQuery<Record<string, unknown>>({
    queryKey: ['presence', conversationId],
    queryFn: async () => {
      const res = await apiFetch<{ success: boolean; data: Record<string, unknown> }>(
        `/api/conversations/${conversationId}/presence`
      )
      return res.data ?? {}
    },
    refetchInterval: activeTab === 'my_active' ? 2_000 : false,
    enabled: !!conv && activeTab === 'my_active' && conv.status !== 'closed',
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

  const clientPresence = useMemo(() => {
    const m = (presenceData ?? conv?.metadata ?? {}) as Record<string, unknown>
    const lastEventAt = typeof m.widget_last_event_at === 'string' ? m.widget_last_event_at : undefined
    return describeClientPresence(presenceFromMetadata(m), lastEventAt, new Date())
  }, [presenceData, conv?.metadata])

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [allMessages.length])

  // Agent reply box: grow 1→6 rows, then scroll internally.
  const autoGrow = () => {
    const el = taRef.current
    if (!el) return
    el.style.height = 'auto'
    const lineHeight = parseInt(getComputedStyle(el).lineHeight || '20', 10)
    const maxH = lineHeight * MAX_TA_ROWS
    el.style.height = `${Math.min(el.scrollHeight, maxH)}px`
    el.style.overflowY = el.scrollHeight > maxH ? 'auto' : 'hidden'
  }
  useEffect(() => { autoGrow() }, [input])

  /**
   * Mirror of the widget's client→operator typing discipline, in the other
   * direction: throttled while typing, cleared the moment the box is empty.
   * The backend key is short-lived, so a silent operator stops the visitor's
   * indicator on its own.
   */
  const reportTyping = (text: string) => {
    if (!text.trim()) {
      lastTypingSentRef.current = 0
      clearAgentTyping(conversationId).catch(() => {})
      return
    }
    const now = Date.now()
    if (now - lastTypingSentRef.current > 1000) {
      lastTypingSentRef.current = now
      postAgentTyping(conversationId, text).catch(() => {})
    }
  }

  // Leaving the conversation must not leave a stale "is typing…" behind.
  useEffect(() => {
    return () => {
      lastTypingSentRef.current = 0
      clearAgentTyping(conversationId).catch(() => {})
    }
  }, [conversationId])

  const handleSend = async () => {
    if (!input.trim() || sending) return
    setSending(true)
    lastTypingSentRef.current = 0
    clearAgentTyping(conversationId).catch(() => {})
    try {
      await sendAgentMessage(conversationId, input.trim())
      setInput('')
      queryClient.invalidateQueries({ queryKey: ['conversation', conversationId] })
      queryClient.invalidateQueries({ queryKey: ['messages-incremental', conversationId] })
    } catch (_err) {
      // send failed silently — user can retry
    } finally { setSending(false) }
  }

  const QUICK_EMOJIS = ['🙂', '😊', '👍', '🙏', '✈️', '💼', '✅', '🎉', '📞', '💬']

  const handleInsertEmoji = (emoji: string) => {
    setInput(prev => `${prev}${emoji}`)
    setShowEmojiPicker(false)
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

  // Block visitor — records phone + email + IP; only phone/email refuse future chats
  const handleBlockConfirm = async () => {
    if (blocking) return
    setBlocking(true)
    setBlockResult(null)
    try {
      const res = await blockConversationVisitor(conversationId)
      const kinds = res.blocked?.length ? res.blocked.join(', ') : 'nothing'
      setBlockResult(`Blocked: ${kinds}`)
      setBlockDialogOpen(false)
    } catch (err) {
      const msg = (err as ApiError)?.message ?? 'Failed to block visitor.'
      setBlockResult(msg)
    } finally {
      setBlocking(false)
    }
  }

  const handleMarkLeadCreated = async () => {
    if (!lead?.id || markingLead) return
    setMarkingLead(true)
    setMarkLeadError(null)
    try {
      await apiFetch(`/api/leads/${lead.id}/mark-crm-created`, { method: 'PATCH' })
      queryClient.invalidateQueries({ queryKey: ['conversation', conversationId] })
      onConversationChange?.()
    } catch (err) {
      const msg = (err as ApiError)?.message ?? 'Failed to create lead. Please try again.'
      setMarkLeadError(msg)
    } finally {
      setMarkingLead(false)
    }
  }

  const copyId = () => {
    navigator.clipboard.writeText(conversationId)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  if (loading) return <div className="h-full flex items-center justify-center text-muted-foreground text-sm">Loading...</div>

  // Bug 5: differentiate "genuine not found" from "transient error".
  // The backend endpoint may return {success:false, data:null} with HTTP 200
  // when a query fails — that used to render identically to a real 404.
  if (isError) {
    const status = (error as ApiError)?.status
    if (status === 403) {
      return (
        <div className="h-full flex flex-col items-center justify-center text-muted-foreground p-8 text-center">
          <p className="text-lg font-semibold text-muted-foreground mb-2">Access restricted</p>
          <p className="text-sm text-muted-foreground">
            This conversation belongs to a different tunnel or team.
          </p>
          <button onClick={onClose} className="mt-4 text-sm text-[#C9A54E] hover:underline">Back to chats</button>
        </div>
      )
    }
    return (
      <div className="h-full flex flex-col items-center justify-center text-muted-foreground p-8 text-center">
        <p className="text-lg font-semibold text-muted-foreground mb-2">Couldn't load conversation</p>
        <p className="text-sm text-muted-foreground">
          Something went wrong loading this conversation. Please try again.
        </p>
        <button onClick={() => refetch()} className="mt-4 text-sm text-[#C9A54E] hover:underline">Retry</button>
      </div>
    )
  }

  if (!conv) return (
    <div className="h-full flex flex-col items-center justify-center text-muted-foreground p-8 text-center">
      <p className="text-lg font-semibold text-muted-foreground mb-2">Conversation not found</p>
      <p className="text-sm text-muted-foreground">
        It may have been deleted or moved. Return to the list to see your current chats.
      </p>
      <button onClick={onClose} className="mt-4 text-sm text-[#C9A54E] hover:underline">Back to chats</button>
    </div>
  )

  const lead: Lead | null | undefined = conv.lead

  if (!permissions.canReadMessages) {
    return (
      <div className='h-full p-6 flex flex-col gap-4 bg-card'>
        <div className='bg-amber-50 border border-amber-200 rounded-lg p-4 text-sm text-amber-800'>
          ⚠️ You are viewing this conversation as a supervisor.
          Message content is not visible. You can reassign this conversation.
        </div>

        <div className='space-y-2 text-sm text-foreground'>
          <p><strong>Visitor:</strong> {conv.visitor_name ?? 'Anonymous'}</p>
          <p><strong>Tunnel:</strong> {conv.tunnel}</p>
          <p><strong>Status:</strong> {conv.status}</p>
          <p><strong>Agent:</strong> {conv.assigned_agent_name ?? conv.assigned_agent_id ?? 'Unassigned'}</p>
          <p><strong>Started:</strong> {conv.created_at ? new Date(conv.created_at).toLocaleString() : '—'}</p>
        </div>

        {permissions.canReassignConversations && (
          <ReassignPanel
            conversationId={conv.id}
            onReassigned={() => {
              queryClient.invalidateQueries({ queryKey: ['conversation', conversationId] })
              onConversationChange?.()
            }}
          />
        )}
      </div>
    )
  }

  return (
    <div className="h-full flex min-w-0 overflow-hidden bg-card text-foreground">

      {/* LEFT COLUMN: Chat (header + messages + input) */}
      <div className="flex min-w-0 flex-1 basis-0 flex-col">

        {/* Header */}
        <div className="flex items-start justify-between px-5 py-4 bg-[#0B1829]">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              {conv.chat_number != null && (
                <span className="text-xs font-mono text-[#C9A54E] shrink-0">#{conv.chat_number}</span>
              )}
              <h2 className="text-base font-semibold text-white truncate">
                {conv.status === 'closed'
                  ? <span className="text-muted-foreground italic">Closed conversation</span>
                  : (role === 'supervisor'
                      ? 'Anonymous Visitor'  // QA: no customer-identifying text in the header
                      : (conv.visitor_name ?? 'Anonymous Visitor'))
                }
              </h2>
              <span className={`inline-flex px-2 py-0.5 rounded text-[10px] font-medium ${
                conv.tunnel === 'sales' ? 'bg-blue-500/20 text-blue-200' : 'bg-purple-500/20 text-purple-200'
              }`}>
                {conv.tunnel}
              </span>
              {conv.tag && (
                <span className="inline-flex px-2 py-0.5 rounded text-[10px] font-medium bg-white/10 text-white/80">
                  {DETAIL_TAG_LABELS[conv.tag] ?? conv.tag}
                </span>
              )}
            </div>
            {/* QA supervisors see NO personal data in the header. */}
            <div className="flex items-center gap-4 mt-1.5">
              {role !== 'supervisor' && conv.visitor_phone && (
                <span className="flex items-center gap-1 text-xs text-muted-foreground">
                  <Phone className="w-3 h-3" />{conv.visitor_phone}
                </span>
              )}
              {role !== 'supervisor' && conv.visitor_email && (
                <span className="flex items-center gap-1 text-xs text-muted-foreground">
                  <Mail className="w-3 h-3" />{conv.visitor_email}
                </span>
              )}
            </div>
            <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 mt-1.5 text-[10px] text-muted-foreground">
              <span className="shrink-0">{allMessages.length} messages</span>
              {(conv.request_id || lead?.id) && (
                <a
                  href={`/leads?highlight=${conv.request_id || lead?.id}`}
                  className="shrink-0 text-[#C9A54E] hover:underline"
                  title="Open request / lead"
                >
                  Request {(conv.request_id || lead?.id || '').slice(0, 8)}…
                </a>
              )}
              {role !== 'supervisor' && (
                <span className="shrink-0">${conv.ai_cost_total.toFixed(4)} AI cost</span>
              )}
              {canViewHistory && (
                <span className="min-w-0 max-w-full truncate">
                  <OperatorBadge conversationId={conversationId} />
                </span>
              )}
              <button onClick={copyId} className="flex items-center gap-0.5 hover:text-foreground transition">
                {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
                {conversationId.slice(0, 8)}...
              </button>
            </div>
          </div>
          <button onClick={onClose} className="ml-3 p-1.5 rounded-lg hover:bg-card/10 text-muted-foreground hover:text-white transition">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3 bg-muted">
          {allMessages.length === 0 ? (
            <div className="text-center text-muted-foreground text-sm py-8">No messages</div>
          ) : allMessages.map(msg => {
            const style = ROLE_STYLES[msg.role] ?? ROLE_STYLES.system
            return (
              <div key={msg.id} className={`flex ${style.align} gap-2`}>
                {msg.role !== 'user' && (
                  <div className="w-7 h-7 rounded-full bg-card border border-border flex items-center justify-center shrink-0 mt-1 shadow-sm">
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
        <div className="shrink-0 border-t border-border bg-card">
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
                <p className="text-sm text-muted-foreground italic leading-relaxed">
                  &ldquo;{typingData.text}&rdquo;
                </p>
              )}
            </div>
          )}

          {/* Agent input — only on My Active */}
          {activeTab === 'my_active' && conv.status !== 'closed' && (
            <div className="px-4 pt-3 pb-2">
              <div className="relative flex gap-2">
                <div className="relative">
                  <button
                    type="button"
                    onClick={() => setShowEmojiPicker(v => !v)}
                    className="h-full px-3 rounded-xl border border-border text-muted-foreground hover:text-foreground hover:border-border transition-all"
                    aria-label="Insert emoji"
                    title="Insert emoji"
                  >
                    <Smile className="w-4 h-4" />
                  </button>
                  {showEmojiPicker && (
                    <div className="absolute bottom-[calc(100%+8px)] left-0 z-20 w-56 rounded-xl border border-border bg-card p-2 shadow-lg">
                      <p className="mb-2 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">Quick emoji</p>
                      <div className="grid grid-cols-5 gap-1">
                        {QUICK_EMOJIS.map((emoji) => (
                          <button
                            key={emoji}
                            type="button"
                            onClick={() => handleInsertEmoji(emoji)}
                            className="rounded-md px-2 py-1.5 text-lg hover:bg-accent"
                            aria-label={`Insert ${emoji}`}
                          >
                            {emoji}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
                <textarea
                  ref={taRef}
                  value={input}
                  onChange={(e) => { setInput(e.target.value); autoGrow(); reportTyping(e.target.value) }}
                  onKeyDown={handleKeyDown}
                  placeholder="Type a reply as agent..."
                  rows={1}
                  disabled={sending}
                  className="flex-1 resize-none rounded-xl border border-input bg-background text-foreground px-4 py-2.5 text-sm focus:outline-none focus:border-[#C9A54E] focus:ring-1 focus:ring-[#C9A54E]/30 placeholder:text-muted-foreground disabled:opacity-50"
                />
                <button onClick={handleSend} disabled={!input.trim() || sending}
                  className="flex items-center gap-1.5 px-4 py-2.5 rounded-xl bg-[#0B1829] text-white text-sm font-medium hover:bg-[#0B1829]/90 disabled:opacity-30 disabled:cursor-not-allowed transition-all shrink-0">
                  <Send className="w-4 h-4" />{sending ? '...' : 'Send'}
                </button>
              </div>
            </div>
          )}

          {/* Take button — only on Queue */}
          {activeTab === 'all_active' && role !== 'qa' && (
            <div className="px-4 py-3">
              <button onClick={handleClaim}
                className="w-full py-2.5 rounded-xl bg-[#C9A54E] text-white text-sm font-semibold hover:bg-[#C9A54E]/90 transition-all">
                Take This Conversation
              </button>
            </div>
          )}

          {lead && permissions.canReadMessages && permissions.canEditLeads && (
            <div className="px-4 py-2">
              {markLeadError && (
                <div className="mb-2 px-2 py-1.5 rounded border border-red-200 bg-red-50 text-[11px] text-red-700">
                  {markLeadError}
                </div>
              )}
              {(() => {
                const hasName  = !!lead.visitor_name?.trim() || !!conv.visitor_name?.trim()
                const hasEmail = !!lead.visitor_email?.trim() || !!conv.visitor_email?.trim()
                const hasPhone = !!lead.visitor_phone?.trim() || !!conv.visitor_phone?.trim()
                const missing: string[] = []
                if (!hasName)  missing.push('name')
                if (!hasEmail) missing.push('email')
                if (!hasPhone) missing.push('phone')
                const isCreated  = lead.created_in_crm === true
                const hasAllData = missing.length === 0
                const isBlockedTag = conv.tag === 'abandoned' || conv.tag === 'no_engagement'

                if (isCreated) {
                  return (
                    <button
                      disabled
                      className="w-full py-2 rounded-lg border border-emerald-400 bg-emerald-100 text-emerald-800 text-xs font-semibold cursor-default flex items-center justify-center gap-1.5"
                    >
                      <Check className="w-3 h-3" />
                      Lead Created
                    </button>
                  )
                }
                if (isBlockedTag) {
                  const reason = conv.tag === 'no_engagement'
                    ? 'Customer left contact but never wrote a message'
                    : 'Customer went quiet before the conversation was completed'
                  return (
                    <button
                      disabled
                      title={`${reason} — CRM submission blocked`}
                      className="w-full py-2 rounded-lg border border-border bg-muted text-muted-foreground text-xs font-medium cursor-not-allowed"
                    >
                      Create Lead ({DETAIL_TAG_LABELS[conv.tag ?? ''] ?? 'Blocked'})
                    </button>
                  )
                }
                if (markingLead) {
                  return (
                    <button
                      disabled
                      className="w-full py-2 rounded-lg border border-border bg-muted text-muted-foreground text-xs font-medium cursor-wait"
                    >
                      Creating...
                    </button>
                  )
                }
                if (!hasAllData) {
                  return (
                    <button
                      disabled
                      title={`Missing: ${missing.join(', ')}`}
                      className="w-full py-2 rounded-lg border border-border bg-muted text-muted-foreground text-xs font-medium cursor-not-allowed"
                    >
                      Create Lead
                    </button>
                  )
                }
                return (
                  <button
                    onClick={handleMarkLeadCreated}
                    className="w-full py-2 rounded-lg bg-[#C9A54E] text-white text-xs font-semibold hover:bg-[#C9A54E]/90 transition-all"
                  >
                    Create Lead
                  </button>
                )
              })()}
            </div>
          )}

          {/* Close button — only on My Active */}
          {activeTab === 'my_active' && conv.status !== 'closed' && (
            <div className="px-4 pb-2">
              <button onClick={() => setCloseDialogOpen(true)}
                className="w-full py-2 rounded-lg border border-border text-muted-foreground text-xs hover:bg-accent hover:text-red-500 transition-all">
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

          {/* Block visitor — moderation action, privileged roles only */}
          {canBlockVisitor && (
            <div className="px-4 pb-2">
              <button
                onClick={() => setBlockDialogOpen(true)}
                disabled={blocking}
                className="w-full py-2 rounded-lg border border-red-200 text-red-500 text-xs hover:bg-red-50 disabled:opacity-50 disabled:cursor-not-allowed transition-all"
              >
                {blocking ? 'Blocking…' : 'Block Visitor'}
              </button>
              {blockResult && (
                <p className="mt-1 text-[10px] text-muted-foreground text-center">{blockResult}</p>
              )}
              <ConfirmDialog
                open={blockDialogOpen}
                onOpenChange={setBlockDialogOpen}
                title="Block this visitor?"
                desc="Block this visitor's IP, phone and email? They will no longer be able to chat or create leads. Note: a shared IP alone never blocks anyone — only the phone and email do."
                confirmText="Block"
                destructive
                handleConfirm={handleBlockConfirm}
              />
            </div>
          )}

          {/* Status bar */}
          <div className="px-4 py-2 flex min-w-0 items-center justify-between gap-2 text-xs text-muted-foreground border-t border-border">
            <span className="min-w-0 truncate">
              Status: <span className={`font-medium ${conv.status === 'active' ? 'text-green-600' : conv.status === 'pending' ? 'text-yellow-600' : 'text-muted-foreground'}`}>{conv.status}</span>
              {' · '}Mode: <span className={`font-medium ${conv.mode === 'human' ? 'text-blue-600' : conv.mode === 'ai' ? 'text-amber-600' : 'text-muted-foreground'}`}>{conv.mode}</span>
              {' · '}Client:
              <span className={`ml-1 inline-flex items-center gap-1 font-medium ${clientPresence.text}`}>
                <span className={`inline-block h-2 w-2 rounded-full ${clientPresence.dot}`} />
                {clientPresence.label}
              </span>
              {conv.agent_state === 'active' && conv.assigned_agent_name && conv.status === 'active' && (
                <span className="ml-2 text-green-600 text-[10px]">● {conv.assigned_agent_name}</span>
              )}
              {conv.agent_state === 'fallback' && conv.engaged_agent_name && (
                <span className="ml-2 text-amber-600 text-[10px]">● {conv.engaged_agent_name} → AI</span>
              )}
              {conv.agent_state === 'ai_only' && conv.mode === 'ai' && conv.status === 'active' && (
                <span className="ml-2 text-amber-500 text-[10px]">● AI handling</span>
              )}
              {conv.mode === 'human' && conv.status === 'active' && conv.agent_state !== 'active' && (
                <span className="ml-2 text-blue-500 text-[10px]">● You are chatting</span>
              )}
            </span>
            <span className="shrink-0">{conv.closed_at ? `Closed ${new Date(conv.closed_at).toLocaleDateString()}` : `Started ${new Date(conv.created_at).toLocaleDateString()}`}</span>
          </div>
        </div>
      </div>

      {/* RIGHT COLUMN: Lead Info Panel (272px, hidden on mobile, admin-only on closed) */}
      {(isAdmin || role === 'qa' || (activeTab !== 'my_closed' && activeTab !== 'all_closed')) && (
      <div className="hidden h-full min-h-0 w-72 shrink-0 overflow-y-auto border-l border-border bg-muted xl:block">
        <div className="p-4 space-y-4">

          {/* AI Summary Card */}
          {conv.summary && (
            <div className="bg-card rounded-xl p-3 border border-border shadow-sm">
              <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground mb-2">
                <FileText className="w-3.5 h-3.5" />
                AI Summary
              </div>
              <p className="text-sm text-foreground leading-relaxed">{conv.summary}</p>
            </div>
          )}

          {/* Lead Score Card */}
          {lead && (
            <div className="bg-card rounded-xl p-3 border border-border shadow-sm">
              <div className="flex items-center justify-between mb-2">
                <span className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
                  <TrendingUp className="w-3.5 h-3.5" />
                  Lead Score
                </span>
                <span className={`px-2 py-0.5 rounded-full text-xs font-semibold border ${TIER_COLORS[lead.tier] ?? TIER_COLORS.bronze}`}>
                  {lead.tier.toUpperCase()}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all ${
                      lead.score >= 80 ? 'bg-yellow-500' : lead.score >= 50 ? 'bg-muted-foreground' : 'bg-orange-400'
                    }`}
                    style={{ width: `${lead.score}%` }}
                  />
                </div>
                <span className="text-sm font-bold text-foreground">{lead.score}</span>
              </div>
              <p className="text-[10px] text-muted-foreground mt-1">
                Status: <span className="font-medium text-muted-foreground">{lead.status}</span>
              </p>
            </div>
          )}

          {/* Route Card */}
          {lead && (lead.origin_code || lead.destination_code) && (
            <div className="bg-card rounded-xl p-3 border border-border shadow-sm">
              <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground mb-2">
                <Plane className="w-3.5 h-3.5" />
                Route
              </div>
              {lead.route_display ? (
                <p className="text-sm font-semibold text-foreground">{lead.route_display}</p>
              ) : (
                <p className="text-sm text-foreground">{lead.origin_code ?? '?'} → {lead.destination_code ?? '?'}</p>
              )}
              <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                {lead.trip_type && lead.trip_type !== 'round_trip' && (
                  <p>Type: <span className="text-foreground">{lead.trip_type.replace(/_/g, ' ')}</span></p>
                )}
                <p>Class: <span className="text-foreground capitalize">{lead.cabin_class}</span></p>
              </div>
            </div>
          )}

          {/* Dates Card */}
          {lead && (lead.departure_date || lead.return_date) && (
            <div className="bg-card rounded-xl p-3 border border-border shadow-sm">
              <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground mb-2">
                <Calendar className="w-3.5 h-3.5" />
                Dates
              </div>
              <div className="space-y-1 text-sm text-foreground">
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
            <div className="bg-card rounded-xl p-3 border border-border shadow-sm">
              <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground mb-2">
                <Users className="w-3.5 h-3.5" />
                Passengers
              </div>
              <p className="text-sm font-medium text-foreground">
                {lead.passengers} {lead.passengers === 1 ? 'traveler' : 'travelers'}
              </p>
            </div>
          )}

          {(() => {
            const site = conv?.metadata?.site as string | undefined
            return site ? (
              <div className="bg-card rounded-xl p-3 border border-border shadow-sm">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-muted-foreground">Source</span>
                  <span className={`inline-flex px-2 py-0.5 rounded text-xs font-medium ${
                    site === 'bbc' ? 'bg-amber-100 text-amber-800' : 'bg-rose-100 text-rose-800'
                  }`}>{site.toUpperCase()}</span>
                </div>
              </div>
            ) : null
          })()}

          {/* Acquisition Details Card */}
          {conv?.metadata && (() => {
            const m = conv.metadata as Record<string, string>
            if (!m.utm_source && !m.gclid && !m.fbclid && !m.referrer && !m.page_url) return null
            return (
              <div className="bg-card rounded-xl p-3 border border-border shadow-sm">
                <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground mb-2">
                  <Globe className="w-3.5 h-3.5" />
                  Acquisition Details
                </div>
                <div className="space-y-1.5 text-xs">
                  {m.utm_source && (
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Source</span>
                      <span className="text-foreground font-medium">{m.utm_source}</span>
                    </div>
                  )}
                  {m.utm_medium && (
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Medium</span>
                      <span className="text-foreground font-medium">{m.utm_medium}</span>
                    </div>
                  )}
                  {m.utm_campaign && (
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Campaign</span>
                      <span className="text-foreground truncate max-w-[160px]" title={m.utm_campaign}>{m.utm_campaign}</span>
                    </div>
                  )}
                  {m.utm_term && (
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Keyword</span>
                      <span className="text-foreground truncate max-w-[160px]" title={m.utm_term}>{m.utm_term}</span>
                    </div>
                  )}
                  {m.gclid && (
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Google Ads</span>
                      <span className="text-green-600 font-medium">✓ gclid</span>
                    </div>
                  )}
                  {m.fbclid && (
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Facebook Ads</span>
                      <span className="text-blue-600 font-medium">✓ fbclid</span>
                    </div>
                  )}
                  {m.referrer && (
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Referrer</span>
                      <span className="text-foreground truncate max-w-[160px]">
                        {(() => { try { return new URL(m.referrer).hostname } catch { return m.referrer } })()}
                      </span>
                    </div>
                  )}
                  {m.page_url && (
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Landing</span>
                      <span className="text-foreground truncate max-w-[160px]">{m.page_url.replace(/^https?:\/\/[^/]+/, '') || '/'}</span>
                    </div>
                  )}
                  {m.google_analytics_client_id && (
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">GA Client</span>
                      <span className="text-foreground truncate max-w-[120px]">{m.google_analytics_client_id}</span>
                    </div>
                  )}
                </div>
              </div>
            )
          })()}

          {/* Contact Card */}
          <div className="bg-card rounded-xl p-3 border border-border shadow-sm">
            <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground mb-2">
              <User className="w-3.5 h-3.5" />
              Contact
            </div>
            <div className="space-y-1.5 text-sm">
              {conv.visitor_name && (
                <p className="flex items-center gap-2 text-foreground">
                  <User className="w-3 h-3 text-muted-foreground shrink-0" />
                  {conv.visitor_name}
                </p>
              )}
              {conv.visitor_phone && (
                <p className="flex items-center gap-2 text-foreground">
                  <Phone className="w-3 h-3 text-muted-foreground shrink-0" />
                  <a href={`tel:${conv.visitor_phone}`} className="text-[#C9A54E] hover:underline">
                    {conv.visitor_phone}
                  </a>
                </p>
              )}
              {conv.visitor_email && (
                <p className="flex items-center gap-2 text-foreground">
                  <Mail className="w-3 h-3 text-muted-foreground shrink-0" />
                  <a href={`mailto:${conv.visitor_email}`} className="text-[#C9A54E] hover:underline text-xs break-all">
                    {conv.visitor_email}
                  </a>
                </p>
              )}
              {!conv.visitor_name && !conv.visitor_phone && !conv.visitor_email && (
                <p className="text-xs text-muted-foreground italic">No contact info yet</p>
              )}
            </div>
          </div>

          {/* Operator History — QA / supervisor / admin */}
          {canViewHistory && (
            <div className="bg-card rounded-xl p-3 border border-border shadow-sm">
              <OperatorHistory conversationId={conversationId} />
            </div>
          )}

          {/* Notes Card */}
          {lead?.notes && lead.notes.trim() !== '' && (
            <div className="bg-card rounded-xl p-3 border border-border shadow-sm">
              <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground mb-2">
                <FileText className="w-3.5 h-3.5" />
                Notes
              </div>
              <p className="text-sm text-muted-foreground whitespace-pre-wrap">{lead.notes}</p>
            </div>
          )}

          {/* Conversation Meta Card */}
          <div className="bg-card rounded-xl p-3 border border-border shadow-sm">
            <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground mb-2">
              <Clock className="w-3.5 h-3.5" />
              Details
            </div>
            <div className="space-y-1 text-xs text-muted-foreground">
              <p>Started: <span className="text-foreground">{new Date(conv.created_at).toLocaleString()}</span></p>
              <p>Messages: <span className="text-foreground">{allMessages.length}</span></p>
              {role !== 'supervisor' && (
                <p>AI cost: <span className="text-foreground">${conv.ai_cost_total.toFixed(4)}</span></p>
              )}
              {conv.assigned_agent_id && (
                <p>Agent: <span className="text-foreground">{conv.assigned_agent_name ?? conv.assigned_agent_id?.slice(0, 8) ?? 'Unassigned'}</span></p>
              )}
            </div>
          </div>

          {/* No Lead placeholder */}
          {!lead && (
            <div className="bg-card rounded-xl p-3 border border-dashed border-border">
              <p className="text-xs text-muted-foreground text-center italic">No lead data captured yet</p>
            </div>
          )}

        </div>
      </div>
      )}

    </div>
  )
}
