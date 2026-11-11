import { useState, useRef, useEffect, useLayoutEffect, useDeferredValue, useCallback, useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Clock, MessageCircle, Search, SlidersHorizontal } from 'lucide-react'
import type { Conversation, ConversationTag } from '@/lib/types'
import { getConversations, getNotifications, apiFetch } from '@/lib/api'
import { stopAssignmentAlerts } from '@/lib/notify-assignment'
import { isEmbedded } from '@/lib/crm-bridge'
import { cn } from '@/lib/utils'
import { Logo } from '@/assets/logo'
import { HeaderActions } from '@/components/header-actions'
import { PageHeader } from '@/components/layout/page-header'
import { isOperatorRole } from '@/components/layout/data/sidebar-data'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { useAttentionStore } from '@/stores/attention-store'
import { useAuthStore } from '@/stores/auth-store'
import { useReadyStore } from '@/stores/ready-store'
import { usePanelModeStore } from '@/stores/panel-mode-store'
import { useReadyToggle } from '@/hooks/use-ready-toggle'
import { QueueSection } from './components/queue-section'
import { TasksBand } from './components/tasks-band'
import { ChatAvatar } from './components/chat-avatar'
import { idleMinutes, IDLE_BADGE_MIN, IDLE_NUDGE_MIN } from './idle'
import ConversationDetail, { type ChatLayout } from './detail'
import { ToRead } from './to-read'
import { useReadState, useToRead } from './to-read-state'
import { unreadCount } from './to-read-view'
import {
  avatarClasses,
  focusKey,
  initials,
  ownerLine,
  ownerOf,
  presenceView,
  previewText,
  routeText,
  rowTime,
  showRail,
  signalClasses,
  signalOf,
  type Signal,
} from './view'

// State (tab) and outcome (tag) are orthogonal axes — a tag chip layers on
// top of whichever state tab is active (e.g. "Abandoned" chip + "Closed"
// tab is a valid, meaningful combination). See TAG_FILTERS below.
const TAG_FILTERS: { key: ConversationTag; label: string }[] = [
  { key: 'fresh', label: 'Fresh' },
  { key: 'active', label: 'Active' },
  { key: 'main_queue', label: 'Main queue' },
  { key: 'completed', label: 'Completed' },
  { key: 'abandoned', label: 'Abandoned' },
  { key: 'no_engagement', label: 'No engagement' },
]
const TAG_LABELS = Object.fromEntries(TAG_FILTERS.map((t) => [t.key, t.label])) as Record<ConversationTag, string>

type TabKey = 'my_active' | 'my_closed' | 'all_active' | 'all_closed' | 'to_read'

const AGENT_TABS: { key: TabKey; label: string; params: Record<string, string> }[] = [
  { key: 'my_active', label: 'Mine', params: { assigned_to: 'me', status: 'active' } },
  { key: 'my_closed', label: 'Closed', params: { assigned_to: 'me', status: 'closed' } },
]

const MANAGER_TABS: { key: TabKey; label: string; params: Record<string, string> }[] = [
  { key: 'my_active', label: 'Mine', params: { assigned_to: 'me', status: 'active' } },
  { key: 'all_active', label: 'All active', params: { assigned_to: 'all', status: 'active' } },
  // Supervisor and owner: this week's lost chats and last week's sample.
  { key: 'to_read', label: 'To read', params: {} },
  { key: 'all_closed', label: 'Closed', params: { assigned_to: 'all', status: 'closed' } },
]

/** To read: supervisors (their team) and the owner, admin, dev (everyone). */
const READ_ROLES = ['owner', 'admin', 'dev', 'supervisor']

/** Container widths where the layout changes: three columns, two, one. */
const WIDE_MIN = 1120
const MEDIUM_MIN = 720

function useWidth(ref: React.RefObject<HTMLElement | null>): number {
  const [w, setW] = useState(() => (typeof window === 'undefined' ? 1440 : window.innerWidth))
  useEffect(() => {
    const el = ref.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver((entries) => setW(entries[0].contentRect.width))
    ro.observe(el)
    return () => ro.disconnect()
  }, [ref])
  return w
}

export function Chats() {
  const roleRaw = useAuthStore((s) => s.auth.user?.role)
  const meId = useAuthStore((s) => s.auth.user?.accountNo)
  const role = Array.isArray(roleRaw) ? roleRaw[0] : (roleRaw ?? 'sales')
  const isManager = ['owner', 'admin', 'dev', 'qa', 'supervisor'].includes(role)
  // Mine hidden for admin/supervisor/qa — they oversee the queue, don't claim conversations
  const hideMyActive = ['admin', 'supervisor', 'qa'].includes(role)
  const canFilterHandled = ['owner', 'admin', 'supervisor', 'qa'].includes(role)
  // Outcome-tag chips: oversight roles only (team-scoped on the backend
  // for supervisors; no_engagement is global regardless of role).
  const canFilterTag = ['owner', 'admin', 'dev', 'supervisor', 'qa'].includes(role)
  const canRead = READ_ROLES.includes(role)
  const visibleTabs = (isManager ? MANAGER_TABS : AGENT_TABS).filter(
    (t) => !(hideMyActive && t.key === 'my_active') && (t.key !== 'to_read' || canRead)
  )
  const embedded = isEmbedded()
  const operator = isOperatorRole(role)
  const { canBeReady, ready, toggle: toggleReady } = useReadyToggle()

  // Highlight from URL param (click from bell dropdown, Your day, a toast)
  const urlHighlight = new URLSearchParams(window.location.search).get('highlight')

  const [activeTab, setActiveTab] = useState<TabKey>(hideMyActive ? 'all_active' : 'my_active')
  const [search, setSearch]       = useState('')
  const [tunnelFilter, setTunnel] = useState('')
  const [handledByFilter, setHandledByFilter] = useState('all')
  const [tagFilter, setTagFilter] = useState<ConversationTag | 'all'>('all')
  const [selectedId, setSelectedId] = useState<string | null>(urlHighlight)
  const [highlightId] = useState<string | null>(urlHighlight)
  const setViewingConversationId = useReadyStore((s) => s.setViewingConversationId)
  const deadlineSeconds = useReadyStore((s) => s.responseDeadlineSeconds)
  const viewingGraceSeconds = useReadyStore((s) => s.viewingGraceSeconds)
  const attentionIds = useAttentionStore((s) => s.attentionIds)
  const dormant = usePanelModeStore((s) => s.dormant)
  const [idleOnly, setIdleOnly] = useState(false)
  const [nowMs, setNowMs] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  // "Wider chat": with a chat open, the list becomes a rail of faces.
  // Remembered per browser.
  const [wide, setWide] = useState(() => {
    try {
      return localStorage.getItem('bbc_wide_chat') === '1'
    } catch {
      return false
    }
  })
  const toggleWide = useCallback(() => {
    setWide((v) => {
      try {
        localStorage.setItem('bbc_wide_chat', v ? '0' : '1')
      } catch {
        /* private mode: the choice holds for this visit */
      }
      return !v
    })
  }, [])
  // Once per session, as specified. sessionStorage (not state) because the
  // banner must not come back on a remount — and not localStorage, because a
  // new shift tomorrow deserves the reminder again.
  const [nudgeDismissed, setNudgeDismissed] = useState(
    () => {
      try {
        return sessionStorage.getItem('bbc_idle_nudge_seen') === '1'
      } catch {
        return false
      }
    },
  )
  const dismissNudge = useCallback(() => {
    setNudgeDismissed(true)
    try {
      sessionStorage.setItem('bbc_idle_nudge_seen', '1')
    } catch {
      /* private mode: the in-memory flag still holds for this mount */
    }
  }, [])

  useEffect(() => {
    setViewingConversationId(selectedId)
  }, [selectedId, setViewingConversationId])

  // Stale conversation IDs (the bell's "waiting" list)
  const { data: notifData } = useQuery({
    queryKey: ['notifications'],
    queryFn: getNotifications,
    refetchInterval: dormant ? false : 30_000,
  })
  const staleIds = new Set(notifData?.stale_conversations?.map(s => s.id) ?? [])
  const debouncedSearch = useDeferredValue(search)
  const queryClient = useQueryClient()

  const containerRef = useRef<HTMLDivElement>(null)
  const width = useWidth(containerRef)
  const layout: ChatLayout = width >= WIDE_MIN ? 'wide' : width >= MEDIUM_MIN ? 'medium' : 'narrow'
  // Only while a chat is open: with none, the whole list (tabs, search) shows.
  const rail = showRail(wide, layout, selectedId)

  // The list, the rail and To read are three different columns. When one
  // turns into another (a chat picked, a tab pressed, the window resized or
  // zoomed across the wide layout) the button that had the focus is removed
  // and the browser drops focus to the page: a keyboard user loses their
  // place. The same chat's button, or the same tab, in the new column takes
  // it. Where the column did not change, the button still has the focus and
  // nothing is done.
  // Read while drawing, before this render's changes reach the page: what
  // holds the focus now. Whatever changes the column is covered, not one
  // handler at a time.
  const focusedNow = focusKey(document.activeElement as HTMLElement | null)
  const focusedBefore = useRef<string | null>(null)
  useLayoutEffect(() => {
    focusedBefore.current = focusedNow
  })
  useEffect(() => {
    const key = focusedBefore.current
    if (!key || (document.activeElement && document.activeElement !== document.body)) return
    Array.from(containerRef.current?.querySelectorAll<HTMLElement>('[data-chat-id], [data-tab]') ?? [])
      .find((el) => focusKey(el) === key)
      ?.focus()
  }, [rail, activeTab])

  // Conversation list — cached per tab, polls every 5s
  const tab = visibleTabs.find(t => t.key === activeTab) ?? visibleTabs[0]
  // with_counts: the tab badges ride this 5 s poll (PR-10) instead of a
  // separate /conversations/counts request every 10 s.
  const listParams: Record<string, string> = { ...tab.params, limit: '50', with_counts: 'true' }
  if (debouncedSearch) listParams.search = debouncedSearch
  if (tunnelFilter) listParams.tunnel = tunnelFilter
  if (handledByFilter !== 'all') listParams.handled_by = handledByFilter
  if (canFilterTag && tagFilter !== 'all') listParams.tag = tagFilter

  // On the default Mine view this key equals ATTENTION_QUERY_KEY in
  // use-heartbeat.ts, so the heartbeat's attention check reuses this cache
  // instead of fetching the same list a second time every 5s. Changing the key
  // shape or the params below without updating that constant reintroduces the
  // duplicate request.
  const reading = activeTab === 'to_read'
  const { data: convResponse, isLoading, isError } = useQuery({
    // eslint-disable-next-line @tanstack/query/exhaustive-deps
    queryKey: ['conversations', activeTab, debouncedSearch, tunnelFilter, handledByFilter, tagFilter],
    queryFn: () => getConversations(listParams),
    enabled: !reading,
    // Bug 3: agents need near-realtime assignment visibility
    refetchInterval: dormant ? false : 5_000,
  })
  // To read: the list (shared with the tab) and what this browser has read.
  const toRead = useToRead(canRead)
  const [readKeys, markRead] = useReadState()
  const toReadUnread = unreadCount(toRead.data, readKeys)
  const conversations: Conversation[] = useMemo(() => convResponse?.data ?? [], [convResponse])

  // Most urgent first: no reply (red), then your turn / just taken (amber),
  // then the rest — and idle desks below the live ones. Stable: the server's
  // order holds inside each group.
  const ordered = useMemo(() => {
    const rows = idleOnly
      ? conversations.filter((c) => (idleMinutes(c) ?? 0) >= IDLE_NUDGE_MIN)
      : [...conversations]
    const urgency = (c: Conversation) => {
      if (activeTab === 'my_closed' || activeTab === 'all_closed') return 3
      const s = signalOf(c, ownerOf(c, meId), nowMs, deadlineSeconds)
      return s ? s.urgency : 2
    }
    const idle = (c: Conversation) => ((idleMinutes(c) ?? 0) >= IDLE_BADGE_MIN ? 1 : 0)
    return rows.sort((a, b) => urgency(a) - urgency(b) || idle(a) - idle(b))
    // nowMs: the countdown re-sorts at most once a second.
  }, [conversations, idleOnly, activeTab, meId, nowMs, deadlineSeconds])

  // Counted on the WHOLE list, not the filtered view: the number must not
  // change just because the operator turned the Idle chip on.
  const idleCount = useMemo(
    () => conversations.filter((c) => (idleMinutes(c) ?? 0) >= IDLE_NUDGE_MIN).length,
    [conversations],
  )
  const showNudge = !dormant && idleCount >= 3 && !nudgeDismissed

  // Counts — from the list response (PR-10). The standalone counts query
  // only runs when the list did not carry them (an older backend, or a
  // count that failed), and then at its old 10 s cadence.
  const listCounts = convResponse?.counts
  const { data: fallbackCounts } = useQuery({
    queryKey: ['conversation-counts', tunnelFilter],
    queryFn: async () => {
      const qs = tunnelFilter ? `?tunnel=${tunnelFilter}` : ''
      const res = await apiFetch<{ success: boolean; data: Partial<Record<TabKey, number>> }>(
        `/api/conversations/counts${qs}`
      )
      return { my_active: 0, my_closed: 0, all_active: 0, all_closed: 0, ...res.data }
    },
    // The To read tab fetches no list, so its badges come from this poll.
    enabled: (reading || !!convResponse) && !listCounts,
    refetchInterval: dormant ? false : 10_000,
  })
  const counts = listCounts
    ? { my_active: 0, my_closed: 0, all_active: 0, all_closed: 0, ...listCounts }
    : (fallbackCounts ?? { my_active: 0, my_closed: 0, all_active: 0, all_closed: 0 })

  // Sound handled globally by notify-assignment.ts (loop ring via heartbeat)

  // Stop ring/flash only when operator has no active assignments
  useEffect(() => {
    const checkAndStop = () => {
      if (!counts?.my_active) stopAssignmentAlerts()
    }
    checkAndStop()
    window.addEventListener('focus', checkAndStop)
    return () => window.removeEventListener('focus', checkAndStop)
  }, [counts?.my_active])

  // Refresh all data on claim/close
  const handleConversationChange = () => {
    setSelectedId(null)
    queryClient.invalidateQueries({ queryKey: ['conversations'] })
    queryClient.invalidateQueries({ queryKey: ['conversation-counts'] })
  }

  // A chat I just took: it is mine now — show it under Mine, keep it open.
  const handleTaken = (id: string) => {
    stopAssignmentAlerts()
    if (!hideMyActive) setActiveTab('my_active')
    setSelectedId(id)
    queryClient.invalidateQueries({ queryKey: ['conversations'] })
    queryClient.invalidateQueries({ queryKey: ['conversation-counts'] })
  }

  const pick = (id: string) => {
    stopAssignmentAlerts()
    setSelectedId(id === selectedId ? null : id)
  }

  const filtersOn = (tunnelFilter ? 1 : 0) + (handledByFilter !== 'all' ? 1 : 0) + (tagFilter !== 'all' ? 1 : 0) + (idleOnly ? 1 : 0)
  const chipCls = (on: boolean) =>
    cn(
      'min-h-11 md:min-h-8 rounded-full border px-3 text-[13px] font-bold transition-colors',
      on ? 'border-kb-202020 bg-kb-202020 text-on-btn' : 'border-k-e3e2e2 bg-k-ffffff text-k-444444 hover:bg-k-f7f7f7'
    )

  const rows = ordered.map(conv => {
    const owner = ownerOf(conv, meId)
    const viewing = conv.id === selectedId
    const sig: Signal | null = activeTab === 'my_closed' || activeTab === 'all_closed'
      ? signalOf(conv, 'closed', nowMs, deadlineSeconds)
      : signalOf(conv, owner, nowMs, viewing ? viewingGraceSeconds : deadlineSeconds)
    return { conv, owner, sig }
  })
  const showGroups = activeTab === 'all_active' && rows.some((r) => r.sig && r.sig.urgency < 2) && rows.some((r) => !r.sig || r.sig.urgency >= 2)
  const nWait = rows.filter((r) => r.sig && r.sig.urgency < 2).length
  const nRed = rows.filter((r) => r.sig?.urgency === 0).length
  const seesBothTunnels = isManager || role === 'project_manager'

  const renderRow = ({ conv, owner, sig }: (typeof rows)[number]) => {
    const on = selectedId === conv.id
    const p = presenceView(conv.client_presence, conv.client_presence_age_seconds)
    const idle = idleMinutes(conv)
    const ownerTxt = activeTab === 'all_active' ? ownerLine(conv, owner) : null
    const attention = attentionIds.includes(conv.id)
    const preview = previewText(conv) || routeText(conv) || (conv.message_count ? `${conv.message_count} messages` : 'No messages yet')
    return (
      <button
        key={conv.id}
        type='button'
        data-chat-id={conv.id}
        aria-pressed={on}
        aria-label={`Conversation ${conv.chat_number != null ? `#${conv.chat_number}` : ''} ${conv.visitor_name ?? 'anonymous visitor'}${sig ? `, ${sig.text}` : ''}`}
        onClick={() => pick(conv.id)}
        className={cn(
          'flex gap-2.5 rounded-[14px] p-2.5 text-start text-k-202020 transition-colors focus-visible:outline-2 focus-visible:outline-k-6ea8ff',
          on ? 'bg-k-ffffff shadow-[0_1px_3px_rgba(32,32,32,0.10)]' : attention ? 'bg-k-fff3e0/70 hover:bg-k-fff3e0' : 'hover:bg-row-hover',
          highlightId === conv.id && !on && 'ring-2 ring-k-f2b01e'
        )}
      >
        <ChatAvatar text={initials(conv.visitor_name)} tone={avatarClasses(owner)} dot={owner !== 'closed' ? p.dot : null} ring={on ? 'border-k-ffffff' : 'border-k-f7f7f7'} />
        <span className='flex min-w-0 flex-1 flex-col gap-[3px]'>
          <span className='flex items-baseline gap-2'>
            <strong className={cn('min-w-0 flex-1 truncate text-sm', !conv.visitor_name && 'font-normal text-k-6b6b6b italic')}>
              {/* Names survive closing: 1292 identical "Closed conversation"
                  rows made review scanning impossible. */}
              {conv.visitor_name ?? 'Anonymous visitor'}
            </strong>
            {conv.has_flagged_content && (
              <span title={conv.flagged_reason || 'Contains flagged content'}>
                <AlertTriangle aria-label='Flagged' className='size-3.5 text-k-d97a00' />
              </span>
            )}
            {isFormTrapSuspect(conv) && <SuspectBadge />}
            <span className='flex-none text-xs text-k-6b6b6b'>{rowTime(conv.updated_at, new Date(nowMs))}</span>
          </span>
          <span className='truncate text-[13px] text-k-555555'>{preview}</span>
          {(ownerTxt || sig || (idle !== null && idle >= IDLE_BADGE_MIN) || staleIds.has(conv.id) || (seesBothTunnels && conv.tunnel === 'support') || conv.tag) && (
            <span className='flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs font-bold'>
              {ownerTxt && <span className={ownerTxt.cls}>{ownerTxt.text}</span>}
              {ownerTxt && sig && <span className='font-normal text-k-9a9a9a'>·</span>}
              {sig && (
                <span className={cn('inline-flex items-center gap-1', signalClasses(sig))}>
                  {sig.clock && <Clock aria-hidden='true' className='size-[13px] flex-none' strokeWidth={2.4} />}
                  {sig.text}
                </span>
              )}
              {/* Both sides silent: only worth a chip when nothing louder is shown. */}
              {!sig && idle !== null && idle >= IDLE_BADGE_MIN && (
                <span title='No message from either side' className='rounded-full bg-k-f0f0f0 px-1.5 py-px font-normal text-k-555555'>
                  Idle {idle}m
                </span>
              )}
              {!sig && staleIds.has(conv.id) && <span className='text-k-b3261e'>Waiting too long</span>}
              {seesBothTunnels && conv.tunnel === 'support' && (
                <span className='rounded-full bg-k-f0f0f0 px-1.5 py-px font-normal text-k-555555'>Support</span>
              )}
              {canFilterTag && conv.tag && tagFilter !== 'all' && (
                <span className='rounded-full bg-k-f0f0f0 px-1.5 py-px font-normal text-k-555555'>{TAG_LABELS[conv.tag]}</span>
              )}
            </span>
          )}
        </span>
      </button>
    )
  }

  const emptyText = isError
    ? null
    : idleOnly
      ? 'No idle chats right now.'
      : debouncedSearch
        ? `No chat matches “${debouncedSearch}”.`
        : tagFilter !== 'all'
          ? `No ${TAG_LABELS[tagFilter].toLowerCase()} chats.`
          : activeTab === 'my_active'
            ? 'You have no open chats. Take one that is waiting.'
            : activeTab === 'all_active'
              ? 'No open chats right now. The assistant is handling the site.'
              : 'No closed chats.'

  const tabBar = (
    <div className='flex flex-wrap gap-2'>
      <div role='tablist' aria-label='Which chats' className='flex min-w-[180px] flex-1 gap-1 rounded-[14px] bg-k-ececec p-1'>
        {visibleTabs.map(t => {
          const n = t.key === 'to_read' ? toReadUnread : counts[t.key as Exclude<TabKey, 'to_read'>]
          return (
            <button
              key={t.key}
              type='button'
              role='tab'
              data-tab={t.key}
              aria-selected={activeTab === t.key}
              onClick={() => { setActiveTab(t.key); setSelectedId(null) }}
              className={cn(
                'flex h-[34px] flex-1 items-center justify-center gap-1.5 rounded-[10px] px-2 text-[13px] whitespace-nowrap text-k-202020 transition-colors',
                activeTab === t.key ? 'bg-k-ffffff font-bold shadow-[0_1px_2px_rgba(32,32,32,0.10)]' : 'hover:bg-k-f4f4f4'
              )}
            >
              {t.label}
              {(t.key !== 'to_read' || n > 0) && (
                <span className={cn('font-normal', (t.key === 'all_active' && n > 0 && !isManager) || (t.key === 'to_read' && n > 0) ? 'text-k-b3261e' : 'text-k-6b6b6b')}>{n}</span>
              )}
            </button>
          )
        })}
      </div>
    </div>
  )

  const listColumn = rail ? (
    <section aria-label='Chats' className='flex w-[84px] flex-none flex-col items-center gap-3 overflow-y-auto rounded-[20px] bg-k-f7f7f7 py-3.5'>
      <QueueSection onClaimed={handleTaken} canTake={!canBeReady || ready} rail />
      <span className='text-[11px] font-bold tracking-[0.05em] text-k-6b6b6b uppercase'>{tab.label === 'All active' ? 'Active' : tab.label}</span>
      {rows.map(({ conv, owner, sig }) => {
        const p = presenceView(conv.client_presence, conv.client_presence_age_seconds)
        return (
          <button
            key={conv.id}
            type='button'
            data-chat-id={conv.id}
            aria-pressed={selectedId === conv.id}
            onClick={() => pick(conv.id)}
            aria-label={`${conv.visitor_name ?? 'Visitor'}${sig ? `, ${sig.text}` : ''}`}
            title={`${conv.visitor_name ?? 'Visitor'}${sig ? ` · ${sig.text}` : ''}`}
            className='relative'
          >
            <ChatAvatar
              text={initials(conv.visitor_name)}
              tone={avatarClasses(owner)}
              dot={owner !== 'closed' ? p.dot : null}
              ring='border-k-f7f7f7'
              size={44}
              className={cn(selectedId === conv.id && 'shadow-[0_0_0_2px_var(--k-f7f7f7),0_0_0_4px_var(--kl-202020)]')}
            />
            {sig && sig.urgency < 2 && (
              <span aria-hidden='true' className={cn('absolute -top-[3px] -end-[3px] size-3 rounded-full border-2 border-k-f7f7f7', sig.urgency === 0 ? 'bg-k-b3261e' : 'bg-k-d97a00')} />
            )}
          </button>
        )
      })}
    </section>
  ) : (
    <section
      aria-label='Chats'
      className={cn(
        'flex min-w-0 flex-col overflow-hidden',
        layout === 'narrow' ? 'flex-1 rounded-none bg-k-ffffff' : 'w-[320px] flex-none rounded-[20px] bg-k-f7f7f7',
        layout === 'narrow' && selectedId && 'hidden'
      )}
    >
      {/* In the CRM dock there is no menu: your follow-ups show over the list. */}
      {embedded && <TasksBand />}
      <div className={cn('flex flex-col gap-3 pb-2.5', layout === 'narrow' ? 'px-0 pt-1' : 'px-3.5 pt-3.5')}>
        <QueueSection onClaimed={handleTaken} canTake={!canBeReady || ready} flat={layout === 'narrow'} />
        {tabBar}
        <div className='flex gap-2'>
          <label className='relative flex flex-1'>
            <span className='sr-only'>Search chats</span>
            <Search aria-hidden='true' className='absolute top-1/2 left-3 size-4 -translate-y-1/2 text-k-6b6b6b' />
            <input
              type='search'
              placeholder='Search a name or #4810'
              value={search}
              onChange={e => setSearch(e.target.value)}
              className='h-10 w-full rounded-xl border-none bg-k-ffffff ps-9 pe-3 text-sm text-k-202020 shadow-[inset_0_0_0_1px_var(--k-e3e2e2)] placeholder:text-k-8a8a8a focus-visible:outline-2 focus-visible:outline-k-6ea8ff'
            />
          </label>
          <Popover>
            <PopoverTrigger asChild>
              <button
                type='button'
                aria-label={filtersOn ? `Filters, ${filtersOn} on` : 'Filters'}
                className={cn(
                  'relative flex size-10 flex-none items-center justify-center rounded-xl text-k-202020 shadow-[inset_0_0_0_1px_var(--k-e3e2e2)]',
                  filtersOn ? 'bg-kb-202020 text-on-btn shadow-none' : 'bg-k-ffffff hover:bg-k-f4f4f4'
                )}
              >
                <SlidersHorizontal aria-hidden='true' className='size-4' />
              </button>
            </PopoverTrigger>
            <PopoverContent align='end' className='flex w-[300px] flex-col gap-3 rounded-2xl p-3.5'>
              {seesBothTunnels && (
                <label className='flex flex-col gap-1 text-xs text-k-555555'>
                  Tunnel
                  <select value={tunnelFilter} onChange={e => setTunnel(e.target.value)}
                    className='h-10 rounded-[10px] border border-k-e3e2e2 bg-k-ffffff px-2.5 text-sm text-k-202020'>
                    <option value=''>Sales and support</option>
                    <option value='sales'>Sales</option>
                    <option value='support'>Support</option>
                  </select>
                </label>
              )}
              {canFilterHandled && (
                <label className='flex flex-col gap-1 text-xs text-k-555555'>
                  Who handled it
                  <select
                    value={handledByFilter}
                    onChange={(e) => setHandledByFilter(e.target.value)}
                    className='h-10 rounded-[10px] border border-k-e3e2e2 bg-k-ffffff px-2.5 text-sm text-k-202020'
                  >
                    <option value='all'>Everyone</option>
                    <option value='ai'>Only the assistant</option>
                    <option value='human'>A person</option>
                    <option value='fallback'>Went back to the assistant</option>
                  </select>
                </label>
              )}
              {canFilterTag && (
                <div className='flex flex-col gap-1.5'>
                  <span className='text-xs text-k-555555'>Outcome</span>
                  <div className='flex flex-wrap gap-1.5'>
                    <button type='button' onClick={() => setTagFilter('all')} className={chipCls(tagFilter === 'all')}>
                      All
                    </button>
                    {TAG_FILTERS.map((t) => (
                      <button key={t.key} type='button' onClick={() => setTagFilter(t.key)} className={chipCls(tagFilter === t.key)}>
                        {t.label}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {/* Idle is a view, not a permission: every operator who can
                  reach it through the nudge's Review must also be able to
                  turn it off. Sales is not in canFilterTag. */}
              <div className='flex flex-col gap-1.5'>
                <span className='text-xs text-k-555555'>Activity</span>
                <div className='flex flex-wrap gap-1.5'>
                  <button type='button' onClick={() => setIdleOnly((v) => !v)} className={chipCls(idleOnly)}>
                    Idle {IDLE_NUDGE_MIN}+ min
                  </button>
                </div>
              </div>
            </PopoverContent>
          </Popover>
        </div>
      </div>

      {/* A desk somebody walked away from. No close-all: only the agent
          who owns a chat closes it, one at a time, after opening it. */}
      {showNudge && (
        <div className='mx-3.5 mb-2 flex items-center justify-between gap-2 rounded-[14px] bg-k-ffffff px-3.5 py-2.5 text-[13px] text-k-444444 shadow-[0_1px_2px_rgba(32,32,32,0.06)]'>
          <span>{idleCount} chats with no activity for over {IDLE_NUDGE_MIN} min.</span>
          <div className='flex flex-none gap-3'>
            <button type='button' className='font-bold text-k-5b45e0' onClick={() => { setIdleOnly(true); dismissNudge() }}>
              Review
            </button>
            <button type='button' className='text-k-6b6b6b' onClick={dismissNudge}>
              Not now
            </button>
          </div>
        </div>
      )}

      <div className={cn('flex min-h-0 flex-1 flex-col overflow-y-auto pb-2.5', layout === 'narrow' ? 'gap-0 px-0 [&>button]:rounded-none [&>button]:border-b [&>button]:border-k-f0f0f0' : 'gap-0.5 px-2')}>
        {isError ? (
          // An error must never wear the empty-state's copy — "No active
          // conversations" on a failed fetch is the panel lying.
          <div role='alert' className='m-1.5 flex items-center gap-2 rounded-[14px] bg-k-fdecea p-4 text-[13px] font-bold text-k-b3261e'>
            <AlertTriangle aria-hidden='true' className='size-4' />
            Couldn&apos;t load conversations — retrying.
          </div>
        ) : isLoading ? (
          <div className='p-4 text-sm text-k-6b6b6b'>Loading…</div>
        ) : rows.length === 0 ? (
          <div className='m-1.5 rounded-[14px] bg-k-ffffff p-[18px] text-sm leading-normal text-k-555555'>{emptyText}</div>
        ) : showGroups ? (
          <>
            <div className='flex items-baseline gap-1.5 px-2.5 pt-1 pb-1 text-[11px] font-bold tracking-[0.05em] text-k-555555 uppercase'>
              Waiting for a reply <span className={nRed ? 'text-k-b3261e' : 'text-k-8a4b00'}>{nWait}</span>
            </div>
            {rows.slice(0, nWait).map(renderRow)}
            <div className='flex items-baseline gap-1.5 px-2.5 pt-3 pb-1 text-[11px] font-bold tracking-[0.05em] text-k-555555 uppercase'>
              The rest <span className='text-k-6b6b6b'>{rows.length - nWait}</span>
            </div>
            {rows.slice(nWait).map(renderRow)}
          </>
        ) : (
          rows.map(renderRow)
        )}
      </div>
    </section>
  )

  const narrowThread = layout === 'narrow' && !!selectedId

  return (
    <main
      id='content'
      data-layout='fixed'
      className={cn(
        'flex min-h-0 flex-1 flex-col gap-3.5',
        narrowThread ? 'p-0' : embedded ? 'px-3 pt-3 pb-3' : 'px-4 pt-5 pb-4 md:px-6 md:pb-6'
      )}
    >
      {!narrowThread &&
        (embedded ? (
          <header className='flex items-center gap-2.5'>
            <Logo alt='' aria-hidden='true' className='size-8 flex-none rounded-[9px]' />
            <h1 className='m-0 flex-1 font-display text-lg font-semibold text-k-202020'>Chats</h1>
            <HeaderActions compact />
          </header>
        ) : (
          <PageHeader size='md' title='Conversations' menuButton={!operator} actions={<HeaderActions compact={layout === 'narrow'} />} />
        ))}

      {!narrowThread && canBeReady && !ready && (
        <div role='status' className='flex flex-wrap items-center gap-x-3.5 gap-y-2 rounded-[14px] bg-k-f7f7f7 py-2.5 ps-4 pe-2.5 text-sm leading-[1.45] text-k-202020'>
          <span className='min-w-[240px] flex-1'>
            <strong>You’re not ready.</strong> No new chats come to you; the ones you have stay yours.
          </span>
          <button type='button' onClick={toggleReady} className='h-10 rounded-full bg-kb-202020 px-[18px] text-sm font-bold text-on-btn'>
            I’m ready
          </button>
        </div>
      )}

      <div ref={containerRef} className={cn('flex min-h-0 flex-1', layout === 'narrow' ? 'gap-0' : 'gap-4')}>
        {reading ? (
          <ToRead
            layout={layout}
            tabBar={tabBar}
            list={toRead.data}
            isError={toRead.isError}
            onRetry={() => toRead.refetch()}
            read={readKeys}
            markRead={markRead}
            scoped={role === 'supervisor'}
          />
        ) : (
        <>
        {listColumn}
        {selectedId ? (
          <div className='min-w-0 flex-1'>
            <ConversationDetail
              conversationId={selectedId}
              onClose={() => setSelectedId(null)}
              activeTab={activeTab}
              onConversationChange={handleConversationChange}
              onTaken={handleTaken}
              layout={layout}
              wide={rail}
              onToggleWide={toggleWide}
            />
          </div>
        ) : (
          layout !== 'narrow' && (
            <section aria-label='Chat' className='flex min-w-0 flex-1 flex-col items-center justify-center gap-2.5 rounded-[20px] bg-k-ffffff p-10 text-center shadow-[0_0_0_1px_var(--k-ececec),0_8px_24px_rgba(32,32,32,0.04)]'>
              <span aria-hidden='true' className='flex size-14 items-center justify-center rounded-full bg-k-f7f7f7'>
                <MessageCircle className='size-6 text-k-6b6b6b' />
              </span>
              <div className='text-lg font-bold text-k-202020'>Pick a chat</div>
              <div className='max-w-[360px] text-sm leading-normal text-k-555555'>Choose a chat on the left, or take one that is waiting.</div>
            </section>
          )
        )}
        </>
        )}
      </div>
    </main>
  )
}

/** PR-12: the pre-chat form's trap fired (hidden field filled, or filled
 *  faster than a person can). Not lost — held from the automatic CRM push
 *  until a human looks. */
function isFormTrapSuspect(conv: Conversation): boolean {
  // The list lifts metadata.suspect to the top level (it carries no metadata);
  // a full conversation object still has it inside metadata.
  const lifted = conv.suspect
  const nested = (conv.metadata as { suspect?: unknown } | undefined)?.suspect
  return (lifted ?? nested) === 'form_trap'
}

function SuspectBadge() {
  return (
    <span
      title="The form's bot check fired. Not sent to the CRM automatically — push it from Leads if it is a real client."
      className='flex-none rounded-full bg-k-f0f0f0 px-1.5 py-px text-xs font-bold text-k-555555'
    >
      Suspect
    </span>
  )
}
