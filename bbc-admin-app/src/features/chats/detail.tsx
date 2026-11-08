import { useState, useEffect, useRef, useMemo } from 'react'
import { toast } from 'sonner'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, ArrowRight, ArrowUp, Clock, Info, Maximize2, Minimize2, MoreHorizontal, Smile } from 'lucide-react'
import type { Message, Lead } from '@/lib/types'
import {
  getConversation,
  sendAgentMessage,
  apiFetch,
  openAgentStream,
  blockConversationVisitor,
  postAgentTyping,
  clearAgentTyping,
  pushLeadToCrm,
  setLeadCrmId,
  type ApiError,
} from '@/lib/api'
import { cn } from '@/lib/utils'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { ReassignPanel } from '@/components/reassign-panel'
import { usePermissions } from '@/lib/bbc/hooks'
import type { UserRole } from '@/lib/bbc/types'
import { useAuthStore } from '@/stores/auth-store'
import { useReadyStore } from '@/stores/ready-store'
import { usePanelModeStore } from '@/stores/panel-mode-store'
import { useReadyToggle } from '@/hooks/use-ready-toggle'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { ChatAvatar } from './components/chat-avatar'
import { CloseChat } from './components/close-chat'
import { DetailsPanel } from './components/details-panel'
import {
  avatarClasses,
  clock,
  closeSentence,
  countdown,
  crmView,
  dayMonth,
  deadlineLeft,
  firstName,
  initials,
  confirmedOf,
  levelOf,
  minutesLabel,
  minutesSince,
  noReplyMinutes,
  ownerOf,
  presenceFromMeta,
  presenceView,
  STALE_CLOSE_MIN,
  takenBlind,
} from './view'

export type ChatLayout = 'wide' | 'medium' | 'narrow'

interface Props {
  conversationId: string
  onClose: () => void
  activeTab?: 'my_active' | 'my_closed' | 'all_active' | 'all_closed'
  onConversationChange?: () => void
  /** The chat became mine (Take from the thread): show it under Mine. */
  onTaken?: (id: string) => void
  usingMock?: boolean
  layout?: ChatLayout
  /** "Wider chat": the list shrinks to a rail and the thread centres. */
  wide?: boolean
  onToggleWide?: () => void
}

const HANDS_ON = ['sales', 'support', 'owner', 'admin', 'dev']

export default function ConversationDetail({
  conversationId,
  onClose,
  activeTab = 'my_active',
  onConversationChange,
  onTaken,
  usingMock,
  layout = 'wide',
  wide = false,
  onToggleWide,
}: Props) {
  const role = (useAuthStore((s) => s.auth.user?.role ?? 'sales') as UserRole)
  const me = useAuthStore((s) => s.auth.user)
  const isAdmin = ['owner', 'admin', 'dev'].includes(role)
  const canViewHistory = ['owner', 'admin', 'dev', 'supervisor', 'qa'].includes(role)
  const permissions = usePermissions(role)
  const canBlockVisitor = ['owner', 'admin', 'dev', 'supervisor', 'project_manager'].includes(role)
  const [closeDialogOpen, setCloseDialogOpen] = useState(false)
  const [closing, setClosing] = useState(false)
  const [blockDialogOpen, setBlockDialogOpen] = useState(false)
  const [blocking, setBlocking] = useState(false)
  const [blockResult, setBlockResult] = useState<string | null>(null)
  const [input, setInput]             = useState('')
  const [showEmojiPicker, setShowEmojiPicker] = useState(false)
  const [sending, setSending] = useState(false)
  const [taking, setTaking] = useState(false)
  // Below the three-column width the details are an overlay toggled from the
  // header — the product's reason-to-exist must not silently vanish.
  const [showDetails, setShowDetails] = useState(false)
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
  // A panel nobody is looking at asks nothing — see stores/panel-mode-store.
  const dormant = usePanelModeStore((s) => s.dormant)
  // While the stream is open the three polls below are off. On three
  // consecutive failures openSse gives up and they come back at exactly
  // today's intervals — never faster.
  const [sseLive, setSseLive] = useState(false)
  const viewingGraceSeconds = useReadyStore((s) => s.viewingGraceSeconds)
  const { canBeReady, ready } = useReadyToggle()
  // One clock for the countdowns and "N min" labels on this screen.
  const [nowMs, setNowMs] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  // Full conversation load — cached, long staleTime
  const { data: conv, isLoading: loading, isError, error, refetch } = useQuery({
    queryKey: ['conversation', conversationId],
    queryFn: () => getConversation(conversationId),
    enabled: !usingMock,
    // Bug 5: do NOT cache null/error — otherwise a single transient Supabase
    // failure poisons the UI with "not found" for 60 seconds. (That concern
    // is about caching FAILURES; retry handles it.) A short staleTime kills
    // the blank "Loading..." flash operators saw on every single chat click
    // — dozens of times per shift — while incremental polling + send/close
    // invalidations keep the thread fresh.
    staleTime: 10_000,
    retry: 1,
    retryDelay: 500,
  })

  // A waiting chat an agent hasn't taken is shown blind (the gate below):
  // nothing about it is polled or streamed for them either, and the server
  // sends it without the words, the lead or the contact (same rule).
  const blindView = !!conv && takenBlind(conv, me?.accountNo, role)

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
    refetchInterval: dormant || sseLive ? false : (activeTab === 'my_active' ? 500 : false),
    enabled: !!conv && !blindView && activeTab === 'my_active' && conv.status !== 'closed',
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
    refetchInterval: dormant || sseLive ? false : (activeTab === 'my_active' ? 2_000 : false),
    enabled: !!conv && !blindView && activeTab === 'my_active',
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
    refetchInterval: dormant || sseLive ? false : (activeTab === 'my_active' ? 2_000 : false),
    enabled: !!conv && !blindView && activeTab === 'my_active' && conv.status !== 'closed',
  })

  // Push instead of ask. One stream for the conversation on screen carries
  // messages, typing and presence — the three things the polls above were
  // asking about, which the server already knew the moment they happened.
  // Bearer over fetch()+ReadableStream, not EventSource: EventSource cannot
  // set headers and a session JWT in a query string lands in every proxy log.
  useEffect(() => {
    // A dormant panel holds no stream (it asks for nothing), and a mock view
    // never opens a real authenticated channel for a conversation it does not
    // query. Both were missing: dormant panels kept server subscriptions alive.
    if (!conversationId || activeTab !== 'my_active' || dormant || usingMock || blindView) return
    const h = openAgentStream(
      conversationId,
      (e) => {
        if (!('event' in e)) {
          // Plain message row. Deduped by id — the incremental poll may have
          // delivered the same row a moment earlier.
          queryClient.setQueryData(
            ['messages-incremental', conversationId],
            (old: Message[] | undefined) =>
              (old ?? []).some((m) => m.id === e.id) ? (old ?? []) : [...(old ?? []), e]
          )
          return
        }
        switch (e.event) {
          case 'typing':
            queryClient.setQueryData(['typing', conversationId], { is_typing: e.is_typing, text: e.text })
            break
          case 'presence': {
            const { event: _ev, ...presence } = e
            queryClient.setQueryData(
              ['presence', conversationId],
              (old: Record<string, unknown> | undefined) => ({ ...(old ?? {}), ...presence })
            )
            break
          }
          case 'stream_chunk':
            // The panel renders whole messages; the widget owns the typewriter.
            break
          case 'stream_end': {
            const { event: _ev, ...row } = e
            queryClient.setQueryData(
              ['messages-incremental', conversationId],
              (old: Message[] | undefined) =>
                (old ?? []).some((m) => m.id === row.id) ? (old ?? []) : [...(old ?? []), row as Message]
            )
            break
          }
        }
      },
      (state) => setSseLive(state === 'open'),
      () => setSseLive(false)
    )
    return () => h.close()
  }, [conversationId, activeTab, dormant, usingMock, blindView, queryClient])

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

  const presence = useMemo(() => {
    const p = presenceFromMeta((presenceData ?? conv?.metadata ?? {}) as Record<string, unknown>)
    return { ...presenceView(p.effective, p.ageSeconds), ageSeconds: p.ageSeconds }
  }, [presenceData, conv?.metadata])

  // New lines, and the client starting to type, both belong in view.
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [allMessages.length, typingData?.is_typing])

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
      // The trust-critical path: a reply to a LIVE customer must never
      // vanish silently. Input is preserved (cleared only on success).
      toast.error('Message NOT delivered — check your connection and press Send again.')
    } finally {
      setSending(false)
      // disabled={sending} dropped the focus when the send started; give
      // it back so the operator keeps typing (or re-presses Send on the
      // error path, where the preserved input is waiting). rAF: focusing
      // a still-disabled textarea is a no-op — the re-enable lands on the
      // next render.
      requestAnimationFrame(() => taRef.current?.focus())
    }
  }

  const QUICK_EMOJIS = ['👍', '🙂', '🙏', '✈️', '✅', '👌', '🎉', '⭐', '😊', '👋']

  const handleInsertEmoji = (emoji: string) => {
    setInput(prev => `${prev}${emoji}`)
    setShowEmojiPicker(false)
    requestAnimationFrame(() => taRef.current?.focus())
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSend() }
  }

  // Claim conversation from queue
  const handleClaim = async () => {
    if (taking) return
    setTaking(true)
    try {
      await apiFetch(`/api/conversations/${conversationId}/claim`, { method: 'POST' })
      queryClient.invalidateQueries({ queryKey: ['conversation', conversationId] })
      if (onTaken) onTaken(conversationId)
      else onConversationChange?.()
    } catch (_err) {
      // Most common cause: another agent grabbed it first (claim race).
      toast.error("Couldn't take this chat — someone may have taken it a moment before you.")
      queryClient.invalidateQueries({ queryKey: ['conversation', conversationId] })
    } finally {
      setTaking(false)
    }
  }

  // Close conversation
  const handleCloseConfirm = async () => {
    setClosing(true)
    try {
      await apiFetch(`/api/conversations/${conversationId}/close`, { method: 'POST' })
      setCloseDialogOpen(false)
      toast.success('Conversation closed.')
      onConversationChange?.()
    } catch (_err) {
      // Dialog stays open — a mute failure here left agents believing
      // conversations were closed when they weren't.
      toast.error("Couldn't close the conversation — try again.")
    } finally {
      setClosing(false)
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

  /** The REAL push (the server runs the gate and the CRM call). A 422 carries
   *  the CRM's refusal; it is shown verbatim. Returns whether it landed. */
  const handleMarkLeadCreated = async (): Promise<boolean> => {
    if (!lead?.id || markingLead) return false
    setMarkingLead(true)
    setMarkLeadError(null)
    try {
      await pushLeadToCrm(lead.id)
      queryClient.invalidateQueries({ queryKey: ['conversation', conversationId] })
      toast.success(`${firstName(conv?.visitor_name)} is in the CRM.`)
      return true
    } catch (err) {
      const msg = (err as ApiError)?.message ?? 'CRM push failed. Please try again.'
      setMarkLeadError(msg)
      return false
    } finally {
      setMarkingLead(false)
    }
  }

  const handleSaveManual = async (crmId: string): Promise<boolean> => {
    if (!lead?.id) return false
    try {
      await setLeadCrmId(lead.id, crmId)
      queryClient.invalidateQueries({ queryKey: ['conversation', conversationId] })
      toast.success(`Saved: CRM lead ${crmId}.`)
      return true
    } catch (err) {
      toast.error((err as ApiError)?.message ?? 'Couldn’t save the lead number. Try again.')
      return false
    }
  }

  const handleSendAndClose = async () => {
    const ok = await handleMarkLeadCreated()
    if (ok) await handleCloseConfirm()
    else toast.error('Not sent to the CRM, so the chat stays open. See the reason under Lead & CRM.')
  }

  if (loading) {
    return (
      <div className='flex h-full items-center justify-center rounded-[20px] bg-k-ffffff text-sm text-k-6b6b6b shadow-[0_0_0_1px_var(--k-ececec)]'>
        Loading…
      </div>
    )
  }

  // Bug 5: differentiate "genuine not found" from "transient error".
  // The backend endpoint may return {success:false, data:null} with HTTP 200
  // when a query fails — that used to render identically to a real 404.
  if (isError) {
    const status = (error as ApiError)?.status
    if (status === 403) {
      return (
        <Notice title='Access restricted' text='This conversation belongs to a different tunnel or team.' action='Back to chats' onAction={onClose} />
      )
    }
    return (
      <Notice title='Couldn’t load the chat' text='Something went wrong loading this conversation. Try again.' action='Retry' onAction={() => refetch()} />
    )
  }

  if (!conv) return (
    <Notice title='Chat not found' text='It may have been deleted or moved. Return to the list to see your current chats.' action='Back to chats' onAction={onClose} />
  )

  const lead: Lead | null | undefined = conv.lead

  if (!permissions.canReadMessages) {
    return (
      <div className='flex h-full flex-col gap-4 rounded-[20px] bg-k-ffffff p-6 shadow-[0_0_0_1px_var(--k-ececec)]'>
        <div className='rounded-2xl bg-k-fff3e0 p-4 text-sm text-k-8a4b00'>
          You can see who has this chat, not what was said. You can give it to someone else.
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

  // ── what this chat is, for me ─────────────────────────────────────────
  const owner = ownerOf(conv, me?.accountNo)
  const first = firstName(conv.visitor_name)
  const isSupervisor = role === 'supervisor'
  const engagedMe = (conv.metadata?.engaged_agent_id as string | undefined) === me?.accountNo
  // My Active lists chats assigned to me AND chats I engaged that went back to
  // the assistant: writing in one of those takes it back (sticky ownership).
  const canWrite = activeTab === 'my_active' && conv.status !== 'closed' && (owner === 'mine' || engagedMe)
  const canTake = owner === 'waiting' && HANDS_ON.includes(role) && conv.status !== 'closed' && (!canBeReady || ready)
  const canManage = permissions.canReassignConversations && conv.status !== 'closed'
  const canActOnLead = isAdmin || (role === 'sales' && (owner === 'mine' || engagedMe))
  const crm = crmView(conv, lead, { canAct: canActOnLead, ownerName: conv.assigned_agent_name ?? null })
  const canPush = !!lead && crm.state === 'ready' && canActOnLead
  const canManual = !!lead && (crm.state === 'ready' || crm.state === 'missing') && canActOnLead
  const viewingDeadline = viewingGraceSeconds
  const left = canWrite ? deadlineLeft(conv, nowMs, viewingDeadline) : null
  const waitingMin = owner === 'waiting' ? minutesSince(conv.queued_at ?? conv.updated_at, nowMs) : null
  const noReply = noReplyMinutes(conv, nowMs)
  const leftMin = presence.state === 'left' && typeof presence.ageSeconds === 'number' ? Math.floor(presence.ageSeconds / 60) : null
  const level = levelOf(lead, confirmedOf(conv))
  const narrow = layout === 'narrow'
  const detailsStatic = layout === 'wide'

  const chip =
    owner === 'closed'
      ? { text: 'Closed', cls: 'bg-k-f0f0f0 text-k-555555' }
      : owner === 'waiting'
        ? { text: `Waiting ${minutesLabel(waitingMin ?? 0)}`, cls: (waitingMin ?? 0) > 5 ? 'bg-k-fdecea text-k-b3261e' : 'bg-k-fff3e0 text-k-8a4b00' }
        : owner === 'ai'
          ? { text: 'AI is answering', cls: 'bg-k-efeaff text-k-4a36c4' }
          : owner === 'agent'
            ? { text: conv.assigned_agent_name ?? 'An agent', cls: 'bg-k-f0f0f0 text-k-444444' }
            : activeTab !== 'my_active'
              ? { text: 'Yours', cls: 'bg-k-00c450/12 text-k-0a6b35' }
              : null

  const note: { text: string; cls: string } | null =
    owner === 'closed'
      ? { text: `This chat is closed${conv.closed_at ? ` since ${clock(conv.closed_at)}` : ''}. It stays in Closed.`, cls: 'bg-k-f7f7f7 text-k-444444' }
      : canWrite || canTake
        ? null
        : owner === 'waiting'
          ? {
              text: canBeReady && !ready ? 'Set yourself ready to take this chat.' : isSupervisor || role === 'project_manager' ? `${first} is waiting for a person. Give the chat to an agent who is ready.` : `${first} is waiting for a person.`,
              cls: 'bg-k-f7f7f7 text-k-444444',
            }
          : owner === 'ai'
            ? { text: `The AI is answering. If ${first} asks for a person, the chat moves to Waiting.`, cls: 'bg-k-f7f7f7 text-k-444444' }
            : owner === 'mine'
              ? { text: 'This chat is yours. Open it under Mine to answer.', cls: 'bg-k-f7f7f7 text-k-444444' }
              : { text: `${conv.assigned_agent_name ?? 'An agent'} is answering. You can read along${canManage ? ', or give the chat to someone else' : ''}.`, cls: 'bg-k-f7f7f7 text-k-444444' }

  // An agent takes a waiting chat blind (owner's decision, Nov 2026): who
  // and how long, never the cities or the client's words until it's theirs.
  // Managers still read it, to give it to the right person. The queue never
  // opens a waiting chat; this covers a link or a selection that lands here.
  // A chat the agent was already talking in (back with the assistant) is
  // theirs to read: they know it.
  if (blindView) {
    const waitedMin = minutesSince(conv.queued_at ?? conv.updated_at, nowMs) ?? 0
    const canTakeBlind = conv.status !== 'closed' && (!canBeReady || ready)
    return (
      <section
        aria-label='Chat'
        className={cn(
          'flex h-full flex-col items-center justify-center gap-4 bg-k-ffffff p-6 text-center',
          narrow ? 'rounded-none' : 'rounded-[20px] shadow-[0_0_0_1px_var(--k-ececec)]'
        )}
      >
        <ChatAvatar text={initials(conv.visitor_name)} tone={avatarClasses('waiting')} dot={presence.dot} size={44} />
        <div className='flex flex-col items-center gap-1.5'>
          <h2 className='m-0 text-[17px] font-bold text-k-202020'>{conv.visitor_name || 'Visitor'}</h2>
          <span className={cn('rounded-full px-2 py-0.5 text-xs font-bold', waitedMin > 5 ? 'bg-k-fdecea text-k-b3261e' : 'bg-k-fff3e0 text-k-8a4b00')}>
            Waiting {minutesLabel(waitedMin)}
          </span>
        </div>
        <p className='m-0 max-w-[340px] text-sm leading-[1.45] text-k-555555'>
          {conv.status === 'needs_agent' ? `${first} asked for a person.` : `${first} is waiting for a person.`} You’ll see the
          chat and the trip once you take it.
        </p>
        {canTakeBlind ? (
          <button type='button' onClick={handleClaim} disabled={taking} className='h-11 rounded-full bg-kb-202020 px-6 text-[15px] font-bold text-on-btn disabled:opacity-50'>
            {taking ? 'Taking…' : 'Take'}
          </button>
        ) : (
          <p className='m-0 text-sm font-bold text-k-202020'>Set yourself ready to take this chat.</p>
        )}
        <button type='button' onClick={onClose} className='min-h-8 text-sm font-bold text-k-5b45e0 hover:underline'>
          Back to chats
        </button>
      </section>
    )
  }

  const myInitials = initials(me?.name ?? 'You')
  const agentName = conv.assigned_agent_name ?? conv.engaged_agent_name ?? null
  const speaker = (r: Message['role']) =>
    r === 'ai'
      ? { name: 'Assistant', av: 'AI', tone: 'bg-k-efeaff text-k-4a36c4' }
      : r === 'agent'
        ? owner === 'mine'
          ? { name: 'You', av: myInitials, tone: 'bg-kb-202020 text-on-btn' }
          : { name: agentName ? firstName(agentName) : 'Agent', av: agentName ? initials(agentName) : 'AG', tone: 'bg-kb-202020 text-on-btn' }
        : { name: first, av: initials(conv.visitor_name), tone: 'bg-k-e6f0fb text-k-1f5fb0' }

  const details = (
    <DetailsPanel
      conv={conv}
      lead={lead}
      owner={owner}
      messages={allMessages}
      isSupervisor={isSupervisor}
      showCost={isAdmin}
      canViewHistory={canViewHistory}
      wide={wide}
      crm={crm}
      canPush={canPush}
      canManual={canManual}
      pushing={markingLead}
      pushError={markLeadError}
      onPush={handleMarkLeadCreated}
      onSaveManual={handleSaveManual}
    />
  )

  const iconBtn =
    'flex size-10 flex-none items-center justify-center rounded-full border border-k-e3e2e2 bg-k-ffffff text-k-202020 hover:bg-k-f7f7f7 focus-visible:outline-2 focus-visible:outline-k-6ea8ff'

  return (
    <div className='relative flex h-full min-w-0 gap-4'>
      <section
        aria-label='Chat'
        className={cn(
          'flex min-w-0 flex-1 flex-col overflow-hidden bg-k-ffffff',
          narrow ? 'rounded-none' : 'rounded-[20px] shadow-[0_0_0_1px_var(--k-ececec),0_8px_24px_rgba(32,32,32,0.04)]'
        )}
      >
        {/* Header */}
        <div className={cn('flex items-center gap-2.5 border-b border-k-f0f0f0', narrow ? 'px-2 py-2.5' : layout === 'wide' ? 'flex-wrap px-[18px] py-3.5' : 'px-4 py-3')}>
          {narrow && (
            <button type='button' onClick={onClose} aria-label='Back to the chats' className='flex size-10 flex-none items-center justify-center rounded-full text-k-202020 hover:bg-k-f7f7f7'>
              <ArrowLeft aria-hidden='true' className='size-5' />
            </button>
          )}
          <ChatAvatar text={initials(conv.visitor_name)} tone={avatarClasses(owner)} dot={owner !== 'closed' ? presence.dot : null} size={42} />
          <div className={cn('flex flex-1 flex-col gap-0.5', layout === 'wide' ? 'min-w-[120px]' : 'min-w-0')}>
            <div className={cn('flex items-center gap-2', layout === 'wide' ? 'flex-wrap' : 'min-w-0')}>
              <h2 className='m-0 min-w-0 truncate text-[17px] font-bold text-k-202020'>{conv.visitor_name || 'Visitor'}</h2>
              {chip && <span className={cn('rounded-full px-2 py-0.5 text-xs font-bold', chip.cls)}>{chip.text}</span>}
              {conv.has_flagged_content && (
                <span title={conv.flagged_reason || 'Contains flagged content'} className='rounded-full bg-k-fff3e0 px-2 py-0.5 text-xs font-bold text-k-8a4b00'>
                  Flagged
                </span>
              )}
            </div>
            {/* Presence is meaningless on a closed thread. */}
            {conv.status !== 'closed' && presence.label && <div className={cn('text-[13px]', presence.text)}>{presence.label}</div>}
          </div>
          {canWrite && (
            <CloseChat
              open={closeDialogOpen}
              onOpenChange={setCloseDialogOpen}
              first={first}
              sentence={closeSentence(conv, crm, lead)}
              crm={crm}
              canSend={canPush}
              busy={closing || markingLead}
              onClose={handleCloseConfirm}
              onSendAndClose={handleSendAndClose}
            />
          )}
          {canManage && !canWrite && (
            <Popover>
              <PopoverTrigger asChild>
                <button type='button' className='h-10 flex-none rounded-full border border-kl-202020 bg-k-ffffff px-4 text-sm font-bold text-k-202020 hover:bg-k-f7f7f7'>
                  Give to…
                </button>
              </PopoverTrigger>
              <PopoverContent align='end' className='w-auto rounded-2xl p-3'>
                <p className='mb-2 text-[11px] font-bold tracking-[0.04em] text-k-6b6b6b uppercase'>Give to an agent</p>
                <ReassignPanel
                  conversationId={conv.id}
                  onReassigned={() => {
                    queryClient.invalidateQueries({ queryKey: ['conversation', conversationId] })
                    onConversationChange?.()
                  }}
                />
              </PopoverContent>
            </Popover>
          )}
          {/* Below full width the header keeps what matters: Close chat and the details. */}
          {(layout === 'wide' || canBlockVisitor) && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button type='button' aria-label='More actions' className={iconBtn}>
                <MoreHorizontal aria-hidden='true' className='size-[18px]' />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align='end' className='w-60 rounded-2xl p-1.5'>
              <DropdownMenuItem
                className='h-10 rounded-[10px] px-2.5 text-sm'
                onClick={() => {
                  navigator.clipboard.writeText(conv.chat_number != null ? `#${conv.chat_number}` : conversationId).then(
                    () => toast.success('Chat number copied'),
                    () => toast.error('Couldn’t copy the chat number.')
                  )
                }}
              >
                Copy chat number{conv.chat_number != null ? ` (#${conv.chat_number})` : ''}
              </DropdownMenuItem>
              {canBlockVisitor && (
                <DropdownMenuItem className='h-10 rounded-[10px] px-2.5 text-sm font-bold text-k-b3261e' onClick={() => setBlockDialogOpen(true)} disabled={blocking}>
                  {blocking ? 'Blocking…' : 'Block this visitor'}
                </DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
          )}
          {layout === 'wide' && onToggleWide && (
            <button
              type='button'
              aria-pressed={wide}
              aria-label={wide ? 'Back to three columns' : 'Wider chat'}
              title={wide ? 'Back to three columns' : 'Wider chat'}
              onClick={onToggleWide}
              className={cn(iconBtn, wide && 'bg-k-f2f2f2')}
            >
              {wide ? <Minimize2 aria-hidden='true' className='size-[17px]' /> : <Maximize2 aria-hidden='true' className='size-[17px]' />}
            </button>
          )}
          {!detailsStatic && (
            <button
              type='button'
              aria-expanded={showDetails}
              aria-label='Trip and lead'
              title='Trip and lead'
              onClick={() => setShowDetails((v) => !v)}
              className={cn(iconBtn, showDetails && 'bg-k-f2f2f2')}
            >
              <Info aria-hidden='true' className='size-[18px]' />
            </button>
          )}
        </div>

        {/* The trip in one line when the details aren't on screen. */}
        {!detailsStatic && lead && (lead.origin_code || lead.destination_code) && (
          <button
            type='button'
            onClick={() => setShowDetails(true)}
            className='mx-3 mt-2.5 flex flex-col items-start gap-0.5 rounded-[14px] bg-k-f7f7f7 px-3.5 py-2 text-start'
          >
            <span className='text-sm text-k-202020'>
              <strong>{lead.origin_code} → {lead.destination_code}</strong>
              {lead.departure_date && (
                <span className='text-k-555555'>
                  {' · '}
                  {dayMonth(lead.departure_date)}
                  {lead.return_date ? ` – ${dayMonth(lead.return_date)}` : ''}
                </span>
              )}
            </span>
            <span className='flex items-center gap-1.5 text-[13px] font-bold text-k-202020'>
              <span aria-hidden='true' className={cn('size-1.5 rounded-full', crm.dot)} />
              {level.chip} · {crm.state === 'in' ? 'In the CRM' : 'Not in the CRM'}
            </span>
          </button>
        )}

        {/* Messages */}
        <div role='log' aria-live='polite' aria-label='Messages' className={cn('flex min-h-0 flex-1 flex-col overflow-x-hidden overflow-y-auto', narrow ? 'px-3.5 py-3.5' : 'px-[22px] py-[18px]')}>
          <div className={cn('flex w-full flex-col', wide && 'mx-auto max-w-[760px]')}>
            {allMessages.length === 0 ? (
              <div className='py-8 text-center text-sm text-k-6b6b6b'>No messages yet</div>
            ) : allMessages.map((msg, i) => {
              // Day separator: a 3-day conversation must not read as one
              // sitting — timestamps alone are hour:minute.
              const day = new Date(msg.created_at).toDateString()
              const prevDay = i > 0 ? new Date(allMessages[i - 1].created_at).toDateString() : null
              const showDay = day !== prevDay && !Number.isNaN(new Date(msg.created_at).getTime())
              // Grouping: consecutive same-speaker turns read as one turn —
              // avatar and name once, tight gap. A new day starts a new group.
              const grouped = !showDay && i > 0 && allMessages[i - 1].role === msg.role && msg.role !== 'system'
              const who = speaker(msg.role)
              return (
                <div key={msg.id} className={grouped ? 'mt-0.5' : 'mt-3.5 first:mt-0'}>
                  {showDay && (
                    <div className='my-3 flex items-center gap-2.5 text-xs text-k-6b6b6b' aria-hidden>
                      <span className='h-px flex-1 bg-k-f0f0f0' />
                      {new Date(msg.created_at).toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short' })}
                      <span className='h-px flex-1 bg-k-f0f0f0' />
                    </div>
                  )}
                  {msg.role === 'system' ? (
                    <div className='my-1.5 flex items-center gap-2.5 text-xs text-k-6b6b6b'>
                      <span className='h-px flex-1 bg-k-f0f0f0' />
                      <span className='max-w-[80%] text-center'>
                        {msg.content} · {clock(msg.created_at)}
                      </span>
                      <span className='h-px flex-1 bg-k-f0f0f0' />
                    </div>
                  ) : (
                    <div className='flex items-start gap-3'>
                      <span
                        aria-hidden='true'
                        className={cn('flex size-8 flex-none items-center justify-center rounded-full text-xs font-bold', who.tone, grouped && 'invisible')}
                      >
                        {who.av}
                      </span>
                      <div className='flex min-w-0 flex-1 flex-col gap-0.5'>
                        {!grouped && (
                          <span className='flex flex-wrap items-baseline gap-1.5'>
                            <strong className='text-sm text-k-202020'>{who.name}</strong>
                            <span className='text-xs text-k-6b6b6b'>{clock(msg.created_at)}</span>
                            {/* Model ids are ops jargon — admins only, not operators. */}
                            {isAdmin && msg.model_used && <span className='text-[11px] text-k-9a9a9a'>{msg.model_used}</span>}
                          </span>
                        )}
                        <div className={cn('leading-[1.55] whitespace-pre-wrap text-k-202020 [overflow-wrap:anywhere]', wide ? 'text-base' : 'text-[15px]')}>{msg.content}</div>
                      </div>
                    </div>
                  )}
                </div>
              )
            })}

            {/* Typing preview — the client's words as they type them */}
            {typingData?.is_typing && activeTab === 'my_active' && conv.status !== 'closed' && (
              <div className='mt-3 flex flex-col items-start gap-1'>
                <span className='flex items-center gap-1.5 text-xs text-k-6b6b6b'>
                  {first} is typing
                  {/* Smooth opacity wave, not a bounce — the dots breathe. */}
                  <span className='flex items-center gap-[3px]' aria-hidden='true'>
                    <span className='size-[5px] animate-pulse rounded-full bg-k-9c9c9c' style={{ animationDelay: '0ms' }} />
                    <span className='size-[5px] animate-pulse rounded-full bg-k-9c9c9c' style={{ animationDelay: '250ms' }} />
                    <span className='size-[5px] animate-pulse rounded-full bg-k-9c9c9c' style={{ animationDelay: '500ms' }} />
                  </span>
                </span>
                {typingData.text && (
                  <div className='max-w-[78%] rounded-[18px_18px_18px_6px] border border-dashed border-k-cfcfcf px-[13px] py-[9px] text-[15px] leading-normal text-k-6b6b6b italic [overflow-wrap:anywhere]'>
                    {typingData.text}
                  </div>
                )}
              </div>
            )}
            <div ref={bottomRef} />
          </div>
        </div>

        {/* Footer: what's next */}
        <div className={cn('flex flex-col gap-2', narrow ? 'px-2.5 pt-2 pb-2.5' : 'px-4 pt-2.5 pb-4')}>
          <div className={cn('flex w-full flex-col gap-2', wide && 'mx-auto max-w-[760px]')}>
            {canWrite && leftMin !== null && (
              <div className='flex items-center gap-2 px-1 text-[13px] text-k-555555'>
                <span aria-hidden='true' className='size-[7px] rounded-full bg-k-c9c9c9' />
                {first} left the site {minutesLabel(leftMin)} ago. If they don’t come back, the chat closes by itself
                {leftMin >= STALE_CLOSE_MIN ? ' any moment.' : ` in about ${Math.max(1, STALE_CLOSE_MIN - leftMin)} min.`}
              </div>
            )}
            {left !== null && (
              <div role='timer' aria-live='off' className='flex flex-col gap-2 rounded-[14px] bg-k-fff3e0 px-3.5 py-2.5'>
                <span className='flex items-start gap-2 text-sm leading-[1.4] text-k-8a4b00'>
                  <Clock aria-hidden='true' className='mt-0.5 size-4 flex-none' />
                  <span>
                    <strong>Reply within {countdown(left)}.</strong> With no reply by then, the assistant answers again and the chat leaves you.
                  </span>
                </span>
                <span aria-hidden='true' className='flex h-1 overflow-hidden rounded-full bg-k-d97a00/20'>
                  <span className='rounded-full bg-k-d97a00' style={{ width: `${Math.round((left / viewingDeadline) * 100)}%` }} />
                </span>
              </div>
            )}
            {canWrite && (
              <div className='relative flex items-end gap-2.5 rounded-[22px] bg-k-ffffff py-1.5 ps-[18px] pe-1.5 shadow-[inset_0_0_0_1px_var(--k-e3e2e2)]'>
                {!narrow && (
                  <Popover open={showEmojiPicker} onOpenChange={setShowEmojiPicker}>
                    <PopoverTrigger asChild>
                      <button type='button' aria-label='Insert emoji' className='mb-0.5 -ms-2 flex size-9 flex-none items-center justify-center rounded-full text-k-444444 hover:bg-k-f7f7f7'>
                        <Smile aria-hidden='true' className='size-[18px]' />
                      </button>
                    </PopoverTrigger>
                    <PopoverContent side='top' align='start' className='grid w-auto grid-cols-5 gap-0.5 rounded-[14px] p-2'>
                      {QUICK_EMOJIS.map((emoji) => (
                        <button key={emoji} type='button' onClick={() => handleInsertEmoji(emoji)} aria-label={`Insert ${emoji}`} className='size-9 rounded-[10px] text-lg hover:bg-k-f4f4f4'>
                          {emoji}
                        </button>
                      ))}
                    </PopoverContent>
                  </Popover>
                )}
                <label className='flex min-w-0 flex-1'>
                  <span className='sr-only'>Your reply. Enter sends, Shift and Enter adds a line.</span>
                  <textarea
                    ref={taRef}
                    value={input}
                    onChange={(e) => { setInput(e.target.value); autoGrow(); reportTyping(e.target.value) }}
                    onKeyDown={handleKeyDown}
                    placeholder={`Reply to ${conv.visitor_name?.trim() ? first : 'the client'}…`}
                    rows={1}
                    disabled={sending}
                    className='min-w-0 flex-1 resize-none border-none bg-transparent py-2.5 text-[15px] leading-[1.4] text-k-202020 outline-offset-4 placeholder:text-k-8a8a8a disabled:opacity-50'
                  />
                </label>
                <button
                  type='button'
                  onClick={handleSend}
                  disabled={!input.trim() || sending}
                  aria-label='Send'
                  className={cn(
                    'flex h-10 flex-none items-center justify-center gap-2 rounded-full text-sm font-bold text-on-btn transition-colors',
                    narrow ? 'w-10' : 'px-[18px]',
                    input.trim() && !sending ? 'bg-kb-202020' : 'bg-k-bdbdbd'
                  )}
                >
                  {narrow ? <ArrowUp aria-hidden='true' className='size-[18px]' /> : <>{sending ? 'Sending…' : 'Send'}<ArrowRight aria-hidden='true' className='size-3.5' /></>}
                </button>
              </div>
            )}
            {canTake && (
              <div className='flex flex-wrap items-center gap-x-3.5 gap-y-2.5 rounded-2xl bg-k-fffaf2 py-3 ps-4 pe-3'>
                <span className='min-w-[220px] flex-1 text-sm leading-[1.45] text-k-444444'>
                  <strong className='text-k-202020'>
                    {first} has been waiting {minutesLabel(waitingMin ?? 0)}.
                  </strong>{' '}
                  Take the chat to answer.
                </span>
                <button type='button' onClick={handleClaim} disabled={taking} className='h-11 rounded-full bg-kb-202020 px-6 text-[15px] font-bold text-on-btn disabled:opacity-50'>
                  {taking ? 'Taking…' : 'Take'}
                </button>
              </div>
            )}
            {canManage && !canWrite && noReply !== null && noReply >= 5 && (
              <div role='status' className='flex flex-wrap items-center gap-3 rounded-[14px] bg-k-ffffff px-3.5 py-3 shadow-[0_0_0_1px_var(--k-f0cdc8),0_4px_14px_rgba(179,38,30,0.06)]'>
                <span aria-hidden='true' className='flex size-8 flex-none items-center justify-center rounded-full bg-k-fde8e6 text-k-d93025'>
                  <Clock className='size-[17px]' />
                </span>
                <span className='flex min-w-[200px] flex-1 flex-col gap-0.5 text-sm leading-[1.4] text-k-202020'>
                  <strong>
                    No reply for <span className='text-k-b3261e'>{minutesLabel(noReply)}</span>
                  </strong>
                  <span className='text-[13px] text-k-555555'>
                    {conv.assigned_agent_name ?? 'The agent'} has the chat. {first} wrote last
                    {conv.last_user_message_at ? `, at ${clock(conv.last_user_message_at)}` : ''}.
                  </span>
                </span>
              </div>
            )}
            {note && <div className={cn('rounded-2xl px-4 py-3.5 text-sm leading-[1.45]', note.cls)}>{note.text}</div>}
            {blockResult && <p className='m-0 px-1 text-xs text-k-6b6b6b'>{blockResult}</p>}
          </div>
        </div>
      </section>

      {/* Details: a column at full width, an overlay below it */}
      {detailsStatic ? (
        <aside aria-label='About this visitor' className={cn('flex-none overflow-y-auto rounded-[20px] bg-k-f7f7f7', wide ? 'w-[360px]' : 'w-[300px]')}>
          {details}
        </aside>
      ) : (
        showDetails && (
          <>
            <button type='button' aria-label='Close the details' onClick={() => setShowDetails(false)} className='absolute inset-0 z-20 bg-black/20' />
            <aside
              aria-label='About this visitor'
              className={cn(
                'absolute z-30 overflow-y-auto bg-k-f7f7f7 shadow-2xl',
                narrow ? 'inset-x-0 bottom-0 max-h-[85%] rounded-t-[20px]' : 'inset-y-0 end-0 w-[320px] rounded-[20px]'
              )}
            >
              {details}
            </aside>
          </>
        )
      )}

      <ConfirmDialog
        open={blockDialogOpen}
        onOpenChange={setBlockDialogOpen}
        title='Block this visitor?'
        desc="Block this visitor's IP, phone and email? They will no longer be able to chat or create leads. Note: a shared IP alone never blocks anyone — only the phone and email do."
        confirmText='Block'
        destructive
        handleConfirm={handleBlockConfirm}
      />
    </div>
  )
}

function Notice({ title, text, action, onAction }: { title: string; text: string; action: string; onAction: () => void }) {
  return (
    <div className='flex h-full flex-col items-center justify-center gap-2 rounded-[20px] bg-k-ffffff p-8 text-center shadow-[0_0_0_1px_var(--k-ececec)]'>
      <p className='m-0 text-lg font-bold text-k-202020'>{title}</p>
      <p className='m-0 max-w-sm text-sm text-k-555555'>{text}</p>
      <button type='button' onClick={onAction} className='mt-2 text-sm font-bold text-k-5b45e0 hover:underline'>
        {action}
      </button>
    </div>
  )
}
