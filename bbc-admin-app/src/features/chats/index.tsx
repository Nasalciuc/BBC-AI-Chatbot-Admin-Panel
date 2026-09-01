import { useState, useRef, useEffect, useDeferredValue, useCallback, useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Search, MessageSquare, ChevronRight, Inbox, UserCheck, Archive, AlertTriangle } from 'lucide-react'
import type { Conversation, ConversationTag } from '@/lib/types'
import { getConversations, getNotifications, apiFetch } from '@/lib/api'
import { stopAssignmentAlerts } from '@/lib/notify-assignment'
import { Header } from '@/components/layout/header'
import { HeaderActions } from '@/components/header-actions'
import { Main } from '@/components/layout/main'
import { useAttentionStore } from '@/stores/attention-store'
import { QueueSection } from './components/queue-section'
import { useAuthStore } from '@/stores/auth-store'
import { useReadyStore } from '@/stores/ready-store'
import { usePanelModeStore } from '@/stores/panel-mode-store'
import { formatAge } from '@/lib/format-age'
import { idleMinutes, IDLE_BADGE_MIN, IDLE_NUDGE_MIN } from './idle'
import { listRowDot } from './presence'
import ConversationDetail from './detail'

// Every status tint carries a dark: variant — light-only literals rendered
// near-white-on-pastel in dark mode.
const TUNNEL_STYLES: Record<string, string> = {
  sales:   'bg-blue-50 text-blue-700 border border-blue-200 dark:bg-blue-950 dark:text-blue-300 dark:border-blue-800',
  support: 'bg-purple-50 text-purple-700 border border-purple-200 dark:bg-purple-950 dark:text-purple-300 dark:border-purple-800',
}
// State (tab) and outcome (tag) are orthogonal axes — a tag chip layers on
// top of whichever state tab is active (e.g. "Abandoned" chip + "All Closed"
// tab is a valid, meaningful combination). See TAG_FILTERS below.
const TAG_STYLES: Record<ConversationTag, string> = {
  fresh: 'bg-sky-50 text-sky-700 border border-sky-200 dark:bg-sky-950 dark:text-sky-300 dark:border-sky-800',
  active: 'bg-green-50 text-green-700 border border-green-200 dark:bg-green-950 dark:text-green-300 dark:border-green-800',
  main_queue: 'bg-amber-50 text-amber-800 border border-amber-200 dark:bg-amber-950 dark:text-amber-300 dark:border-amber-800',
  completed: 'bg-emerald-50 text-emerald-700 border border-emerald-200 dark:bg-emerald-950 dark:text-emerald-300 dark:border-emerald-800',
  abandoned: 'bg-slate-100 text-slate-600 border border-slate-300 dark:bg-slate-900 dark:text-slate-400 dark:border-slate-700',
  no_engagement: 'bg-orange-50 text-orange-700 border border-orange-200 dark:bg-orange-950 dark:text-orange-300 dark:border-orange-800',
}
const TAG_LABELS: Record<ConversationTag, string> = {
  fresh: 'Fresh',
  active: 'Active',
  main_queue: 'Main Queue',
  completed: 'Completed',
  abandoned: 'Abandoned',
  no_engagement: 'No engagement',
}
const TAG_FILTERS: { key: ConversationTag; label: string }[] = [
  { key: 'fresh', label: 'Fresh' },
  { key: 'active', label: 'Active' },
  { key: 'main_queue', label: 'Main Queue' },
  { key: 'completed', label: 'Completed' },
  { key: 'abandoned', label: 'Abandoned' },
  { key: 'no_engagement', label: 'No engagement' },
]

type TabKey = 'my_active' | 'my_closed' | 'all_active' | 'all_closed'

const AGENT_TABS: { key: TabKey; label: string; icon: React.ReactNode; params: Record<string, string> }[] = [
  { key: 'my_active', label: 'My Active', icon: <UserCheck className="w-4 h-4" />, params: { assigned_to: 'me', status: 'active' } },
  { key: 'my_closed', label: 'My Closed', icon: <Archive className="w-4 h-4" />, params: { assigned_to: 'me', status: 'closed' } },
]

const MANAGER_TABS: { key: TabKey; label: string; icon: React.ReactNode; params: Record<string, string> }[] = [
  { key: 'my_active', label: 'My Active', icon: <UserCheck className="w-4 h-4" />, params: { assigned_to: 'me', status: 'active' } },
  { key: 'all_active', label: 'All Active', icon: <Inbox className="w-4 h-4" />, params: { assigned_to: 'all', status: 'active' } },
  { key: 'all_closed', label: 'All Closed', icon: <Archive className="w-4 h-4" />, params: { assigned_to: 'all', status: 'closed' } },
]

const timeAgo = (iso: string): string => formatAge(iso, new Date()) ?? ''

export function Chats() {
  const roleRaw = useAuthStore((s) => s.auth.user?.role)
  const role = Array.isArray(roleRaw) ? roleRaw[0] : (roleRaw ?? 'sales')
  const isManager = ['owner', 'admin', 'dev', 'qa', 'supervisor'].includes(role)
  // My Active hidden for admin/supervisor/qa — they oversee the queue, don't claim conversations
  const hideMyActive = ['admin', 'supervisor', 'qa'].includes(role)
  const canFilterHandled = ['owner', 'admin', 'supervisor', 'qa'].includes(role)
  // Outcome-tag chip bar: oversight roles only (team-scoped on the backend
  // for supervisors; no_engagement is global regardless of role).
  const canFilterTag = ['owner', 'admin', 'dev', 'supervisor', 'qa'].includes(role)
  const visibleTabs = (isManager ? MANAGER_TABS : AGENT_TABS).filter(
    (t) => !(hideMyActive && t.key === 'my_active')
  )

  // Highlight from URL param (click from bell dropdown)
  const urlHighlight = new URLSearchParams(window.location.search).get('highlight')

  const [activeTab, setActiveTab] = useState<TabKey>(hideMyActive ? 'all_active' : 'my_active')
  const [search, setSearch]       = useState('')
  const [tunnelFilter, setTunnel] = useState('')
  const [handledByFilter, setHandledByFilter] = useState('all')
  const [tagFilter, setTagFilter] = useState<ConversationTag | 'all'>('all')
  const [selectedId, setSelectedId] = useState<string | null>(urlHighlight)
  const [highlightId] = useState<string | null>(urlHighlight)
  const setViewingConversationId = useReadyStore((s) => s.setViewingConversationId)
  const attentionIds = useAttentionStore((s) => s.attentionIds)
  const dormant = usePanelModeStore((s) => s.dormant)
  const [idleOnly, setIdleOnly] = useState(false)
  const [nudgeDismissedAt, setNudgeDismissedAt] = useState(0)

  useEffect(() => {
    setViewingConversationId(selectedId)
  }, [selectedId, setViewingConversationId])

  // Stale conversation IDs for red highlight
  const { data: notifData } = useQuery({
    queryKey: ['notifications'],
    queryFn: getNotifications,
    refetchInterval: dormant ? false : 30_000,
  })
  const staleIds = new Set(notifData?.stale_conversations?.map(s => s.id) ?? [])
  const debouncedSearch = useDeferredValue(search)
  const queryClient = useQueryClient()

  // Resizable panel — persists in localStorage
  const [leftWidth, setLeftWidth] = useState<number>(() => {
    try {
      const saved = localStorage.getItem('bbc_panel_width')
      return saved ? Math.min(600, Math.max(280, parseInt(saved))) : 384
    } catch { return 384 }
  })
  const isResizing = useRef(false)
  const containerRef = useRef<HTMLDivElement>(null)

  const startResize = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    isResizing.current = true
    const onMouseMove = (e: MouseEvent) => {
      if (!isResizing.current || !containerRef.current) return
      const rect = containerRef.current.getBoundingClientRect()
      const newWidth = Math.min(600, Math.max(280, e.clientX - rect.left))
      setLeftWidth(newWidth)
    }
    const onMouseUp = () => {
      isResizing.current = false
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('mouseup', onMouseUp)
    }
    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseup', onMouseUp)
  }, [])

  useEffect(() => {
    try { localStorage.setItem('bbc_panel_width', String(leftWidth)) } catch { /* ignore */ }
  }, [leftWidth])

  // Conversation list — cached per tab, polls every 30s
  const tab = visibleTabs.find(t => t.key === activeTab) ?? visibleTabs[0]
  const listParams: Record<string, string> = { ...tab.params, limit: '50' }
  if (debouncedSearch) listParams.search = debouncedSearch
  if (tunnelFilter) listParams.tunnel = tunnelFilter
  if (handledByFilter !== 'all') listParams.handled_by = handledByFilter
  if (canFilterTag && tagFilter !== 'all') listParams.tag = tagFilter

  // On the default My Active view this key equals ATTENTION_QUERY_KEY in
  // use-heartbeat.ts, so the heartbeat's attention check reuses this cache
  // instead of fetching the same list a second time every 5s. Changing the key
  // shape or the params below without updating that constant reintroduces the
  // duplicate request.
  const { data: convResponse, isLoading, isError } = useQuery({
    // eslint-disable-next-line @tanstack/query/exhaustive-deps
    queryKey: ['conversations', activeTab, debouncedSearch, tunnelFilter, handledByFilter, tagFilter],
    queryFn: () => getConversations(listParams),
    // Bug 3: agents need near-realtime assignment visibility
    refetchInterval: dormant ? false : 5_000,
  })
  const conversations: Conversation[] = convResponse?.data ?? []

  // Live conversations first, idle ones below — a stable secondary sort that
  // keeps the server's order (and the needs-attention/stale ordering) intact
  // inside each group.
  const ordered = useMemo(() => {
    const rows = idleOnly
      ? conversations.filter((c) => (idleMinutes(c) ?? 0) >= IDLE_NUDGE_MIN)
      : [...conversations]
    return rows.sort((a, b) => {
      const ai = (idleMinutes(a) ?? 0) >= IDLE_BADGE_MIN ? 1 : 0
      const bi = (idleMinutes(b) ?? 0) >= IDLE_BADGE_MIN ? 1 : 0
      return ai - bi
    })
  }, [conversations, idleOnly])

  // Counted on the WHOLE list, not the filtered view: the number must not
  // change just because the operator turned the Idle chip on.
  const idleCount = useMemo(
    () => conversations.filter((c) => (idleMinutes(c) ?? 0) >= IDLE_NUDGE_MIN).length,
    [conversations],
  )
  const showNudge = !dormant && idleCount >= 3 && idleCount > nudgeDismissedAt

  // Counts — 1 request for tab badges, polls every 10s
  const { data: counts = { my_active: 0, my_closed: 0, all_active: 0, all_closed: 0 } } = useQuery({
    queryKey: ['conversation-counts', tunnelFilter],
    queryFn: async () => {
      const qs = tunnelFilter ? `?tunnel=${tunnelFilter}` : ''
      const res = await apiFetch<{ success: boolean; data: Partial<Record<TabKey, number>> }>(
        `/api/conversations/counts${qs}`
      )
      return { my_active: 0, my_closed: 0, all_active: 0, all_closed: 0, ...res.data }
    },
    refetchInterval: dormant ? false : 10_000,
  })

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

  return (
    <>
      <Header>
        <HeaderActions />
      </Header>
      <Main fixed>
        <div ref={containerRef} className="flex h-full overflow-hidden rounded-lg border border-border">
          {/* Left panel — tabs + list. On mobile the detail takes over the
              whole screen (master-detail); a fixed 384px column inside a
              375px viewport was clipping every control. */}
          <div
            data-panel="left"
            className={`${selectedId ? 'hidden md:flex' : 'flex'} flex-col border-r border-border bg-card`}
            style={selectedId ? { width: leftWidth, minWidth: 280, maxWidth: 480, flexShrink: 0 } : { flex: 1 }}
          >
            {/* Tabs */}
            <div className="flex border-b border-border">
              {visibleTabs.map(tab => (
                <button
                  key={tab.key}
                  onClick={() => { setActiveTab(tab.key); setSelectedId(null) }}
                  className={`flex-1 flex items-center justify-center gap-2 px-3 py-3 text-xs font-medium transition-colors border-b-2 ${
                    activeTab === tab.key
                      ? 'border-[#C9A54E] text-foreground'
                      : 'border-transparent text-muted-foreground hover:text-foreground'
                  }`}
                >
                  {tab.icon}
                  {tab.label}
                  {counts[tab.key] > 0 && (
                    <span className={`ml-1 px-1.5 py-0.5 rounded-full text-[11px] font-bold ${
                      tab.key === 'all_active' && counts[tab.key] > 0
                        ? 'bg-red-500 text-white animate-pulse'
                        : 'bg-muted text-muted-foreground'
                    }`}>
                      {counts[tab.key]}
                    </span>
                  )}
                </button>
              ))}
            </div>

            {/* Shared queue — waiting for ANY operator; first click wins */}
            <QueueSection
              onClaimed={(id) => {
                stopAssignmentAlerts()
                setSelectedId(id)
              }}
            />

            {/* Search + Tunnel filter */}
            <div className="px-4 py-3 border-b border-border space-y-2">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <input type="text" placeholder="Search visitor or #1042..." value={search} onChange={e => setSearch(e.target.value)}
                  className="w-full pl-9 pr-3 py-2 text-sm rounded-lg border border-input bg-background text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-[#C9A54E]/40" />
              </div>
              <select value={tunnelFilter} onChange={e => setTunnel(e.target.value)}
                className="w-full pl-3 pr-7 py-1.5 text-xs rounded-lg appearance-none border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring/40 capitalize">
                <option value="">All Tunnels</option>
                <option value="sales">Sales</option>
                <option value="support">Support</option>
              </select>
              {canFilterHandled && (
                <select
                  value={handledByFilter}
                  onChange={(e) => setHandledByFilter(e.target.value)}
                  className="w-full pl-3 pr-7 py-1.5 text-xs rounded-lg appearance-none border border-input bg-background text-foreground focus:outline-none focus:ring-2 focus:ring-ring/40"
                >
                  <option value="all">All handling</option>
                  <option value="ai">AI only</option>
                  <option value="human">Human</option>
                  <option value="fallback">Fallback</option>
                </select>
              )}
              {canFilterTag && (
                <div className="flex flex-wrap gap-1.5 pt-0.5">
                  <button
                    onClick={() => setTagFilter('all')}
                    className={`px-2 py-1 min-h-11 md:min-h-0 rounded-full text-[11px] font-medium border transition-colors ${
                      tagFilter === 'all'
                        ? 'bg-[#0B1829] text-white border-[#0B1829]'
                        : 'bg-background text-muted-foreground border-input hover:text-foreground'
                    }`}
                  >
                    All
                  </button>
                  <button
                    onClick={() => setIdleOnly((v) => !v)}
                    className={`px-2 py-1 min-h-11 md:min-h-0 rounded-full text-[11px] font-medium border transition-colors ${
                      idleOnly
                        ? 'bg-[#0B1829] text-white border-[#0B1829]'
                        : 'bg-background text-muted-foreground border-input hover:text-foreground'
                    }`}
                  >
                    Idle
                  </button>
                  {TAG_FILTERS.map((t) => (
                    <button
                      key={t.key}
                      onClick={() => setTagFilter(t.key)}
                      className={`px-2 py-1 min-h-11 md:min-h-0 rounded-full text-[11px] font-medium border transition-colors ${
                        tagFilter === t.key
                          ? `${TAG_STYLES[t.key]} ring-1 ring-offset-1 ring-current`
                          : 'bg-background text-muted-foreground border-input hover:text-foreground'
                      }`}
                    >
                      {t.label}
                    </button>
                  ))}
                </div>
              )}
            </div>

            {/* A desk somebody walked away from. No close-all: only the agent
                who owns a chat closes it, one at a time, after opening it. */}
            {showNudge && (
              <div className="mx-3 my-2 rounded-md border bg-muted/40 px-3 py-2 text-[12px] flex items-center justify-between gap-2">
                <span>{idleCount} chats with no activity for over {IDLE_NUDGE_MIN} min.</span>
                <div className="flex gap-3 shrink-0">
                  <button className="underline" onClick={() => setIdleOnly(true)}>Review</button>
                  <button
                    className="text-muted-foreground"
                    onClick={() => setNudgeDismissedAt(idleCount)}
                  >
                    Not now
                  </button>
                </div>
              </div>
            )}

            {/* Conversation list */}
            <div className="flex-1 overflow-y-auto divide-y divide-border">
              {isError ? (
                // An error must never wear the empty-state's copy — "No active
                // conversations" on a failed fetch is the panel lying.
                <div className="flex flex-col items-center justify-center h-32 gap-1 text-red-600">
                  <AlertTriangle className="w-5 h-5" />
                  <p className="text-xs font-medium">Couldn&apos;t load conversations — retrying.</p>
                </div>
              ) : isLoading ? (
                <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">Loading...</div>
              ) : ordered.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-32 text-muted-foreground">
                  <MessageSquare className="w-6 h-6 mb-1 opacity-30" />
                  <p className="text-xs">
                    {tagFilter !== 'all'
                      ? `No ${TAG_LABELS[tagFilter].toLowerCase()} conversations`
                      : activeTab === 'all_closed' || activeTab === 'my_closed'
                        ? 'No closed conversations'
                        : 'No active conversations'}
                  </p>
                </div>
              ) : ordered.map(conv => (
                <button key={conv.id}
                  aria-label={`Conversation ${conv.chat_number != null ? `#${conv.chat_number}` : ''} ${conv.visitor_name ?? 'anonymous visitor'}`}
                  onClick={() => {
                    stopAssignmentAlerts()
                    setSelectedId(conv.id === selectedId ? null : conv.id)
                  }}
                  className={`w-full text-left px-4 py-3 hover:bg-accent transition-colors ${selectedId === conv.id ? 'bg-[#0B1829]/5 border-l-2 border-[#C9A54E]' : staleIds.has(conv.id) ? 'bg-red-50 border-l-2 border-red-400' : attentionIds.includes(conv.id) ? 'bg-amber-50 dark:bg-amber-900/20 border-l-2 border-amber-400' : ''} ${highlightId === conv.id ? 'ring-2 ring-amber-400' : ''}`}>
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className={`w-2 h-2 rounded-full shrink-0 ${listRowDot(conv.status, conv.updated_at, new Date())}`} />
                        {conv.chat_number != null && (
                          <span className="text-[11px] font-mono text-muted-foreground shrink-0">#{conv.chat_number}</span>
                        )}
                        <span className="font-medium text-sm text-foreground truncate">
                          {/* Names survive closing: 1292 identical "Closed
                              conversation" rows made review scanning
                              impossible — and gained no privacy (contact
                              details render in the detail header anyway). */}
                          {<span className='flex items-center gap-1'>
                                {staleIds.has(conv.id) && (
                                  <AlertTriangle className='h-3 w-3 text-red-400 shrink-0' />
                                )}
                                {conv.visitor_name ?? <span className="text-muted-foreground italic text-xs">Anonymous visitor</span>}
                                {conv.has_flagged_content && (
                                  <span
                                    title={conv.flagged_reason || 'Contains flagged content'}
                                    className='text-amber-500 text-xs font-bold ml-1'
                                  >
                                    ⚠️
                                  </span>
                                )}
                              </span>
                          }
                        </span>
                      </div>
                      <div className="flex items-center gap-2 mt-1 flex-wrap">
                        {conv.tag && (
                          <span className={`inline-flex px-1.5 py-0.5 rounded text-[11px] font-medium ${TAG_STYLES[conv.tag]}`}>
                            {TAG_LABELS[conv.tag]}
                          </span>
                        )}
                        <span className={`inline-flex px-1.5 py-0.5 rounded text-[11px] font-medium ${TUNNEL_STYLES[conv.tunnel] ?? ''}`}>{conv.tunnel}</span>
                        <span className="text-[11px] text-muted-foreground">{conv.message_count} msgs</span>
                        {(() => {
                          const idle = idleMinutes(conv)
                          return idle !== null && idle >= IDLE_BADGE_MIN ? (
                            <span
                              title="No message from either side"
                              className="inline-flex px-1.5 py-0.5 rounded text-[11px] font-medium bg-muted text-muted-foreground"
                            >
                              Idle {idle}m
                            </span>
                          ) : null
                        })()}
                        {conv.agent_state === 'active' && conv.assigned_agent_name ? (
                          <span className="text-[11px] text-green-700">● {conv.assigned_agent_name}</span>
                        ) : conv.agent_state === 'fallback' && conv.engaged_agent_name ? (
                          <span className="text-[11px] text-amber-700">● {conv.engaged_agent_name} → AI</span>
                        ) : conv.mode === 'ai' ? (
                          <span className="text-[11px] text-amber-700">● AI</span>
                        ) : null}
                      </div>
                    </div>
                    <div className="flex flex-col items-end gap-1 shrink-0">
                      <span className="text-[11px] text-muted-foreground whitespace-nowrap">{timeAgo(conv.updated_at)}</span>
                      <ChevronRight className={`w-3 h-3 text-muted-foreground transition-transform ${selectedId === conv.id ? 'rotate-90' : ''}`} />
                    </div>
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* Drag handle */}
          {selectedId && (
            <div
              onMouseDown={startResize}
              className="hidden md:block w-1.5 shrink-0 bg-border hover:bg-[#C9A54E]/60 active:bg-[#C9A54E] transition-colors"
              style={{ cursor: 'col-resize' }}
              title="Drag to resize"
            />
          )}

          {/* Right panel — detail */}
          {selectedId ? (
            <div className="flex-1 min-w-0 overflow-hidden">
              <ConversationDetail
                conversationId={selectedId}
                onClose={() => setSelectedId(null)}
                activeTab={activeTab}
                onConversationChange={handleConversationChange}
              />
            </div>
          ) : (
            <div className="flex-1 hidden md:flex flex-col items-center justify-center text-muted-foreground">
              <MessageSquare className="w-12 h-12 mb-3 opacity-20" />
              <p className="text-sm">Select a conversation to view</p>
            </div>
          )}
        </div>
      </Main>
    </>
  )
}
