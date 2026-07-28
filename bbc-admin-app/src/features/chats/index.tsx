import { useState, useRef, useEffect, useDeferredValue, useCallback } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Search, MessageSquare, ChevronRight, Inbox, UserCheck, Archive, AlertTriangle, Moon } from 'lucide-react'
import type { Conversation, ConversationTag } from '@/lib/types'
import { getConversations, getNotifications, apiFetch } from '@/lib/api'
import { stopAssignmentAlerts } from '@/lib/notify-assignment'
import { NotificationBell } from '@/components/notification-bell'
import { ReadyToggle } from '@/components/ready-toggle'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { ConnectionBanner } from '@/components/connection-banner'
import { ProfileDropdown } from '@/components/profile-dropdown'
import { ThemeSwitch } from '@/components/theme-switch'
import { useAuthStore } from '@/stores/auth-store'
import { useReadyStore } from '@/stores/ready-store'
import ConversationDetail from './detail'

const TUNNEL_STYLES: Record<string, string> = {
  sales:   'bg-blue-50 text-blue-700 border border-blue-200',
  support: 'bg-purple-50 text-purple-700 border border-purple-200',
}
const STATUS_DOT: Record<string, string> = {
  active: 'bg-green-400', pending: 'bg-yellow-400', closed: 'bg-muted-foreground/50',
  needs_agent: 'bg-red-400',
}
const TAG_STYLES: Record<ConversationTag, string> = {
  fresh: 'bg-sky-50 text-sky-700 border border-sky-200',
  active: 'bg-emerald-50 text-emerald-700 border border-emerald-200',
  main_queue: 'bg-amber-50 text-amber-800 border border-amber-200',
  inactive: 'bg-slate-100 text-slate-600 border border-slate-300',
}
const TAG_LABELS: Record<ConversationTag, string> = {
  fresh: 'Fresh',
  active: 'Active',
  main_queue: 'Main Queue',
  inactive: 'Inactive',
}

type TabKey = 'my_active' | 'my_closed' | 'all_active' | 'all_closed' | 'inactive'

const AGENT_TABS: { key: TabKey; label: string; icon: React.ReactNode; params: Record<string, string> }[] = [
  { key: 'my_active', label: 'My Active', icon: <UserCheck className="w-4 h-4" />, params: { assigned_to: 'me', status: 'active' } },
  { key: 'my_closed', label: 'My Closed', icon: <Archive className="w-4 h-4" />, params: { assigned_to: 'me', status: 'closed' } },
]

const MANAGER_TABS: { key: TabKey; label: string; icon: React.ReactNode; params: Record<string, string> }[] = [
  { key: 'my_active', label: 'My Active', icon: <UserCheck className="w-4 h-4" />, params: { assigned_to: 'me', status: 'active' } },
  { key: 'all_active', label: 'All Active', icon: <Inbox className="w-4 h-4" />, params: { assigned_to: 'all', status: 'active' } },
  { key: 'inactive', label: 'Inactive', icon: <Moon className="w-4 h-4" />, params: { tag: 'inactive' } },
  { key: 'all_closed', label: 'All Closed', icon: <Archive className="w-4 h-4" />, params: { assigned_to: 'all', status: 'closed' } },
]

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

export function Chats() {
  const roleRaw = useAuthStore((s) => s.auth.user?.role)
  const role = Array.isArray(roleRaw) ? roleRaw[0] : (roleRaw ?? 'sales')
  const isManager = ['owner', 'admin', 'dev', 'qa', 'supervisor'].includes(role)
  // My Active hidden for admin/supervisor/qa — they oversee the queue, don't claim conversations
  const hideMyActive = ['admin', 'supervisor', 'qa'].includes(role)
  const canFilterHandled = ['owner', 'admin', 'supervisor', 'qa'].includes(role)
  // Inactive section: supervisors + admins (team-scoped on the backend for supervisors)
  const canSeeInactive = ['owner', 'admin', 'dev', 'supervisor', 'qa'].includes(role)
  const visibleTabs = (isManager ? MANAGER_TABS : AGENT_TABS).filter(
    (t) => !(hideMyActive && t.key === 'my_active') && !(t.key === 'inactive' && !canSeeInactive)
  )

  // Highlight from URL param (click from bell dropdown)
  const urlHighlight = new URLSearchParams(window.location.search).get('highlight')

  const [activeTab, setActiveTab] = useState<TabKey>(hideMyActive ? 'all_active' : 'my_active')
  const [search, setSearch]       = useState('')
  const [tunnelFilter, setTunnel] = useState('')
  const [handledByFilter, setHandledByFilter] = useState('all')
  const [selectedId, setSelectedId] = useState<string | null>(urlHighlight)
  const [highlightId] = useState<string | null>(urlHighlight)
  const setViewingConversationId = useReadyStore((s) => s.setViewingConversationId)

  useEffect(() => {
    setViewingConversationId(selectedId)
  }, [selectedId, setViewingConversationId])

  // Stale conversation IDs for red highlight
  const { data: notifData } = useQuery({
    queryKey: ['notifications'],
    queryFn: getNotifications,
    refetchInterval: 30_000,
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

  // On the default My Active view this key equals ATTENTION_QUERY_KEY in
  // use-heartbeat.ts, so the heartbeat's attention check reuses this cache
  // instead of fetching the same list a second time every 5s. Changing the key
  // shape or the params below without updating that constant reintroduces the
  // duplicate request.
  const { data: convResponse, isLoading } = useQuery({
    // eslint-disable-next-line @tanstack/query/exhaustive-deps
    queryKey: ['conversations', activeTab, debouncedSearch, tunnelFilter, handledByFilter],
    queryFn: () => getConversations(listParams),
    refetchInterval: 5_000, // Bug 3: agents need near-realtime assignment visibility
  })
  const conversations: Conversation[] = convResponse?.data ?? []

  // Counts — 1 request for tab badges, polls every 10s
  const { data: counts = { my_active: 0, my_closed: 0, all_active: 0, all_closed: 0, inactive: 0 } } = useQuery({
    queryKey: ['conversation-counts', tunnelFilter],
    queryFn: async () => {
      const qs = tunnelFilter ? `?tunnel=${tunnelFilter}` : ''
      const res = await apiFetch<{ success: boolean; data: Partial<Record<TabKey, number>> }>(
        `/api/conversations/counts${qs}`
      )
      return { my_active: 0, my_closed: 0, all_active: 0, all_closed: 0, inactive: 0, ...res.data }
    },
    refetchInterval: 10_000,
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
        <div className='ms-auto flex items-center space-x-4'>
          <ConnectionBanner />
          <ReadyToggle />
          <NotificationBell />
          <ThemeSwitch />
          <ProfileDropdown />
        </div>
      </Header>
      <Main fixed>
        <div ref={containerRef} className="flex h-full overflow-hidden rounded-lg border border-border">
          {/* Left panel — tabs + list */}
          <div
            data-panel="left"
            className="flex flex-col border-r border-border bg-card"
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
                    <span className={`ml-1 px-1.5 py-0.5 rounded-full text-[10px] font-bold ${
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
            </div>

            {/* Conversation list */}
            <div className="flex-1 overflow-y-auto divide-y divide-border">
              {isLoading ? (
                <div className="flex items-center justify-center h-32 text-muted-foreground text-sm">Loading...</div>
              ) : conversations.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-32 text-muted-foreground">
                  <MessageSquare className="w-6 h-6 mb-1 opacity-30" />
                  <p className="text-xs">
                    {activeTab === 'inactive'
                      ? 'No inactive conversations'
                      : activeTab === 'all_closed' || activeTab === 'my_closed'
                        ? 'No closed conversations'
                        : 'No active conversations'}
                  </p>
                </div>
              ) : conversations.map(conv => (
                <button key={conv.id}
                  onClick={() => {
                    stopAssignmentAlerts()
                    setSelectedId(conv.id === selectedId ? null : conv.id)
                  }}
                  className={`w-full text-left px-4 py-3 hover:bg-accent transition-colors ${selectedId === conv.id ? 'bg-[#0B1829]/5 border-l-2 border-[#C9A54E]' : staleIds.has(conv.id) ? 'bg-red-50 border-l-2 border-red-400' : ''} ${highlightId === conv.id ? 'ring-2 ring-amber-400' : ''}`}>
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className={`w-2 h-2 rounded-full shrink-0 ${STATUS_DOT[conv.status] ?? 'bg-muted-foreground/50'}`} />
                        {conv.chat_number != null && (
                          <span className="text-[10px] font-mono text-muted-foreground shrink-0">#{conv.chat_number}</span>
                        )}
                        <span className="font-medium text-sm text-foreground truncate">
                          {activeTab === 'my_closed' || activeTab === 'all_closed'
                            ? <span className="text-muted-foreground italic text-xs">Closed conversation</span>
                            : <span className='flex items-center gap-1'>
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
                          <span className={`inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium ${TAG_STYLES[conv.tag]}`}>
                            {TAG_LABELS[conv.tag]}
                          </span>
                        )}
                        <span className={`inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium ${TUNNEL_STYLES[conv.tunnel] ?? ''}`}>{conv.tunnel}</span>
                        <span className="text-[10px] text-muted-foreground">{conv.message_count} msgs</span>
                        {conv.agent_state === 'active' && conv.assigned_agent_name ? (
                          <span className="text-[10px] text-green-600">● {conv.assigned_agent_name}</span>
                        ) : conv.agent_state === 'fallback' && conv.engaged_agent_name ? (
                          <span className="text-[10px] text-amber-600">● {conv.engaged_agent_name} → AI</span>
                        ) : conv.mode === 'ai' ? (
                          <span className="text-[10px] text-amber-500">● AI</span>
                        ) : null}
                      </div>
                    </div>
                    <div className="flex flex-col items-end gap-1 shrink-0">
                      <span className="text-[10px] text-muted-foreground whitespace-nowrap">{timeAgo(conv.updated_at)}</span>
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
              className="w-1.5 shrink-0 bg-border hover:bg-[#C9A54E]/60 active:bg-[#C9A54E] transition-colors"
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
