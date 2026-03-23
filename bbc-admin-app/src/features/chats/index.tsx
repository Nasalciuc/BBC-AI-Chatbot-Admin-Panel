import { useState, useEffect, useCallback } from 'react'
import { Search, MessageSquare, ChevronDown, ChevronRight } from 'lucide-react'
import type { Conversation } from '@/lib/types'
import { getConversations } from '@/lib/api'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { ConnectionBanner } from '@/components/connection-banner'
import { ProfileDropdown } from '@/components/profile-dropdown'
import { ThemeSwitch } from '@/components/theme-switch'
import ConversationDetail from './detail'

const TUNNEL_STYLES: Record<string, string> = {
  sales:   'bg-blue-50 text-blue-700 border border-blue-200',
  support: 'bg-purple-50 text-purple-700 border border-purple-200',
}
const STATUS_DOT: Record<string, string> = {
  active: 'bg-green-400', pending: 'bg-yellow-400', closed: 'bg-gray-300',
  needs_agent: 'bg-red-400',
}

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
  const [conversations, setConversations] = useState<Conversation[]>([])
  const [total, setTotal]                 = useState(0)
  const [loading, setLoading]             = useState(true)
  const [usingMock, setUsingMock]         = useState(false)
  const [search, setSearch]               = useState('')
  const [tunnelFilter, setTunnel]         = useState('')
  const [statusFilter, setStatus]         = useState('')
  const [selectedId, setSelectedId]       = useState<string | null>(null)

  const fetchConversations = useCallback(async () => {
    setLoading(true)
    try {
      const params: Record<string, string> = { limit: '50' }
      if (search)       params.search = search
      if (tunnelFilter) params.tunnel = tunnelFilter
      if (statusFilter) params.status = statusFilter
      const json = await getConversations(params)
      setConversations(json.data); setTotal(json.count); setUsingMock(false)
    } catch (err) {
      console.error('[chats] API error:', err)
      setConversations([]); setTotal(0); setUsingMock(false)
    } finally { setLoading(false) }
  }, [search, tunnelFilter, statusFilter])

  useEffect(() => { fetchConversations() }, [fetchConversations])

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
          {/* Left panel — list */}
          <div className={`flex flex-col border-r border-gray-200 bg-white transition-all duration-200 ${selectedId ? 'w-96 min-w-[24rem]' : 'flex-1'}`}>
            <div className="px-4 py-4 border-b border-gray-100 space-y-2">
              <div className="flex items-center justify-between">
                <h1 className="text-xl font-bold text-[#0B1829]">Conversations</h1>
                <span className="text-xs text-gray-400">{total}{usingMock && ' · mock'}</span>
              </div>
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                <input type="text" placeholder="Search visitor..." value={search} onChange={e => setSearch(e.target.value)}
                  className="w-full pl-9 pr-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-[#C9A54E]/40" />
              </div>
              <div className="flex gap-2">
                {[
                  { key: 'tunnel', val: tunnelFilter, setter: setTunnel, opts: ['', 'sales', 'support'] },
                  { key: 'status', val: statusFilter, setter: setStatus, opts: ['', 'active', 'needs_agent', 'pending', 'closed'] },
                ].map(({ key, val, setter, opts }) => (
                  <div key={key} className="relative flex-1">
                    <select value={val} onChange={e => setter(e.target.value)}
                      className="w-full pl-3 pr-7 py-1.5 text-xs border border-gray-200 rounded-lg appearance-none bg-white focus:outline-none capitalize">
                      {opts.map(o => <option key={o} value={o}>{o || `All ${key}`}</option>)}
                    </select>
                    <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 w-3 h-3 text-gray-400 pointer-events-none" />
                  </div>
                ))}
              </div>
            </div>

            {(() => {
              const needsAgentCount = conversations.filter(c => c.status === 'needs_agent').length
              return !statusFilter && needsAgentCount > 0 ? (
                <div className="flex items-center gap-2 border-b border-red-100 bg-red-50 px-4 py-2.5">
                  <span className={`inline-flex h-5 min-w-5 items-center justify-center rounded-full bg-red-500 px-1 text-[10px] font-bold text-white ${needsAgentCount > 0 ? 'animate-pulse' : ''}`}>
                    {needsAgentCount}
                  </span>
                  <span className="text-sm font-medium text-red-800">
                    conversation{needsAgentCount > 1 ? 's' : ''} need agent attention
                  </span>
                  <button onClick={() => setStatus('needs_agent')}
                    className="ml-auto text-xs font-medium text-red-700 hover:underline">
                    Show →
                  </button>
                </div>
              ) : null
            })()}
            <div className="flex-1 overflow-y-auto divide-y divide-gray-50">
              {loading ? (
                <div className="flex items-center justify-center h-32 text-gray-400 text-sm">Loading...</div>
              ) : conversations.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-32 text-gray-400">
                  <MessageSquare className="w-6 h-6 mb-1 opacity-30" /><p className="text-xs">No conversations</p>
                </div>
              ) : conversations.map(conv => (
                <button key={conv.id}
                  onClick={() => setSelectedId(conv.id === selectedId ? null : conv.id)}
                  className={`w-full text-left px-4 py-3 hover:bg-gray-50 transition-colors ${selectedId === conv.id ? 'bg-[#0B1829]/5 border-l-2 border-[#C9A54E]' : ''}`}>
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className={`w-2 h-2 rounded-full shrink-0 ${STATUS_DOT[conv.status]}`} />
                        <span className="font-medium text-sm text-gray-900 truncate">
                          {conv.visitor_name ?? <span className="text-gray-400 italic text-xs">Anonymous visitor</span>}
                        </span>
                      </div>
                      <div className="flex items-center gap-2 mt-1">
                        <span className={`inline-flex px-1.5 py-0.5 rounded text-[10px] font-medium ${TUNNEL_STYLES[conv.tunnel]}`}>{conv.tunnel}</span>
                        <span className="text-[10px] text-gray-400">{conv.message_count} msgs · ${conv.ai_cost_total.toFixed(4)}</span>
                      </div>
                      {conv.visitor_email && <p className="text-xs text-gray-400 mt-0.5 truncate">{conv.visitor_email}</p>}
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
              <ConversationDetail conversationId={selectedId} onClose={() => setSelectedId(null)} usingMock={usingMock} />
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
