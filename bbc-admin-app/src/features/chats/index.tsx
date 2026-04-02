import { useState, useRef, useEffect, useDeferredValue } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Search, MessageSquare, ChevronRight, Inbox, UserCheck, Archive } from 'lucide-react'
import type { Conversation } from '@/lib/types'
import { getConversations, apiFetch } from '@/lib/api'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { ConnectionBanner } from '@/components/connection-banner'
import { ProfileDropdown } from '@/components/profile-dropdown'
import { ThemeSwitch } from '@/components/theme-switch'
import { useAuthStore } from '@/stores/auth-store'
import ConversationDetail from './detail'

const TUNNEL_STYLES: Record<string, string> = {
  sales:   'bg-blue-50 text-blue-700 border border-blue-200',
  support: 'bg-purple-50 text-purple-700 border border-purple-200',
}
const STATUS_DOT: Record<string, string> = {
  active: 'bg-green-400', pending: 'bg-yellow-400', closed: 'bg-gray-300',
  needs_agent: 'bg-red-400',
}

type TabKey = 'my_active' | 'queue' | 'my_closed'

const TABS: { key: TabKey; label: string; icon: React.ReactNode; params: Record<string, string> }[] = [
  { key: 'my_active', label: 'My Active',  icon: <UserCheck className="w-4 h-4" />, params: { assigned_to: 'me', status: 'active' } },
  { key: 'queue',     label: 'All',         icon: <Inbox className="w-4 h-4" />,     params: { status: 'active' } },
  { key: 'my_closed', label: 'My Closed',   icon: <Archive className="w-4 h-4" />,   params: { assigned_to: 'me', status: 'closed' } },
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
  const isManager = ['owner', 'admin', 'dev'].includes(role)
  const visibleTabs = isManager ? TABS : TABS.filter(t => t.key !== 'queue')

  const [activeTab, setActiveTab] = useState<TabKey>('my_active')
  const [search, setSearch]       = useState('')
  const [tunnelFilter, setTunnel] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const debouncedSearch = useDeferredValue(search)
  const queryClient = useQueryClient()

  // Conversation list — cached per tab, polls every 30s
  const tab = visibleTabs.find(t => t.key === activeTab) ?? visibleTabs[0]
  const listParams: Record<string, string> = { ...tab.params, limit: '50' }
  if (debouncedSearch) listParams.search = debouncedSearch
  if (tunnelFilter) listParams.tunnel = tunnelFilter

  const { data: convResponse, isLoading } = useQuery({
    queryKey: ['conversations', activeTab, debouncedSearch, tunnelFilter],
    queryFn: () => getConversations(listParams),
    refetchInterval: 30_000,
  })
  const conversations: Conversation[] = convResponse?.data ?? []

  // Counts — 1 request for 3 numbers, polls every 10s
  const { data: counts = { my_active: 0, queue: 0, my_closed: 0 } } = useQuery({
    queryKey: ['conversation-counts', tunnelFilter],
    queryFn: async () => {
      const qs = tunnelFilter ? `?tunnel=${tunnelFilter}` : ''
      const res = await apiFetch<{ success: boolean; data: Record<TabKey, number> }>(
        `/api/conversations/counts${qs}`
      )
      return res.data
    },
    refetchInterval: 10_000,
  })

  // Notification sound when my_active grows (new conversation assigned)
  const prevActiveRef = useRef(-1)
  useEffect(() => {
    if (prevActiveRef.current >= 0 && counts.my_active > prevActiveRef.current) {
      new Audio('/notification.wav').play().catch(() => {})
    }
    prevActiveRef.current = counts.my_active
  }, [counts.my_active])

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
          <ThemeSwitch />
          <ProfileDropdown />
        </div>
      </Header>
      <Main fixed>
        <div className="flex h-full overflow-hidden rounded-lg border border-gray-200">
          {/* Left panel — tabs + list */}
          <div className={`flex flex-col border-r border-gray-200 bg-white transition-all duration-200 ${selectedId ? 'w-96 min-w-[24rem]' : 'flex-1'}`}>
            {/* Tabs */}
            <div className="flex border-b border-gray-200">
              {visibleTabs.map(tab => (
                <button
                  key={tab.key}
                  onClick={() => { setActiveTab(tab.key); setSelectedId(null) }}
                  className={`flex-1 flex items-center justify-center gap-2 px-3 py-3 text-xs font-medium transition-colors border-b-2 ${
                    activeTab === tab.key
                      ? 'border-[#C9A54E] text-[#0B1829]'
                      : 'border-transparent text-gray-400 hover:text-gray-600'
                  }`}
                >
                  {tab.icon}
                  {tab.label}
                  {counts[tab.key] > 0 && (
                    <span className={`ml-1 px-1.5 py-0.5 rounded-full text-[10px] font-bold ${
                      tab.key === 'queue' && counts[tab.key] > 0
                        ? 'bg-red-500 text-white animate-pulse'
                        : 'bg-gray-100 text-gray-600'
                    }`}>
                      {counts[tab.key]}
                    </span>
                  )}
                </button>
              ))}
            </div>

            {/* Search + Tunnel filter */}
            <div className="px-4 py-3 border-b border-gray-100 space-y-2">
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                <input type="text" placeholder="Search visitor..." value={search} onChange={e => setSearch(e.target.value)}
                  className="w-full pl-9 pr-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-[#C9A54E]/40" />
              </div>
              <select value={tunnelFilter} onChange={e => setTunnel(e.target.value)}
                className="w-full pl-3 pr-7 py-1.5 text-xs border border-gray-200 rounded-lg appearance-none bg-white focus:outline-none capitalize">
                <option value="">All Tunnels</option>
                <option value="sales">Sales</option>
                <option value="support">Support</option>
              </select>
            </div>

            {/* Conversation list */}
            <div className="flex-1 overflow-y-auto divide-y divide-gray-50">
              {isLoading ? (
                <div className="flex items-center justify-center h-32 text-gray-400 text-sm">Loading...</div>
              ) : conversations.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-32 text-gray-400">
                  <MessageSquare className="w-6 h-6 mb-1 opacity-30" />
                  <p className="text-xs">
                    {activeTab === 'queue' ? 'No active conversations' : activeTab === 'my_closed' ? 'No closed conversations' : 'No active conversations'}
                  </p>
                </div>
              ) : conversations.map(conv => (
                <button key={conv.id}
                  onClick={() => setSelectedId(conv.id === selectedId ? null : conv.id)}
                  className={`w-full text-left px-4 py-3 hover:bg-gray-50 transition-colors ${selectedId === conv.id ? 'bg-[#0B1829]/5 border-l-2 border-[#C9A54E]' : ''}`}>
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className={`w-2 h-2 rounded-full shrink-0 ${STATUS_DOT[conv.status] ?? 'bg-gray-300'}`} />
                        <span className="font-medium text-sm text-gray-900 truncate">
                          {conv.visitor_name ?? <span className="text-gray-400 italic text-xs">Anonymous visitor</span>}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 mt-1">
                        <span className={`inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium ${TUNNEL_STYLES[conv.tunnel] ?? ''}`}>{conv.tunnel}</span>
                        <span className="text-[10px] text-gray-400">{conv.message_count} msgs</span>
                        {conv.mode === 'ai' && <span className="text-[10px] text-amber-500">● AI</span>}
                      </div>
                    </div>
                    <div className="flex flex-col items-end gap-1 shrink-0">
                      <span className="text-[10px] text-gray-400 whitespace-nowrap">{timeAgo(conv.updated_at)}</span>
                      <ChevronRight className={`w-3 h-3 text-gray-300 transition-transform ${selectedId === conv.id ? 'rotate-90' : ''}`} />
                    </div>
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* Right panel — detail */}
          {selectedId ? (
            <div className="flex-1 overflow-hidden">
              <ConversationDetail
                conversationId={selectedId}
                onClose={() => setSelectedId(null)}
                activeTab={activeTab}
                onConversationChange={handleConversationChange}
              />
            </div>
          ) : (
            <div className="flex-1 hidden md:flex flex-col items-center justify-center text-gray-300">
              <MessageSquare className="w-12 h-12 mb-3 opacity-20" />
              <p className="text-sm">Select a conversation to view</p>
            </div>
          )}
        </div>
      </Main>
    </>
  )
}
