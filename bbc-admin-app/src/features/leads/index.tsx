import { useState, useEffect, useCallback } from 'react'
import { Search, Filter, Phone, Mail, Plane, ChevronDown, RefreshCw } from 'lucide-react'
import type { Lead } from '@/lib/types'
import { getLeads, updateLeadStatus } from '@/lib/api'
import { Header } from '@/components/layout/header'
import { Main } from '@/components/layout/main'
import { ConnectionBanner } from '@/components/connection-banner'
import { ProfileDropdown } from '@/components/profile-dropdown'
import { ThemeSwitch } from '@/components/theme-switch'
import { LeadDetailDrawer } from './components/lead-detail-drawer'
import { ExportLeadsButton } from './components/export-leads-button'

const TIER_STYLES: Record<string, string> = {
  gold:   'bg-yellow-100 text-yellow-800 border border-yellow-300',
  silver: 'bg-gray-100 text-gray-700 border border-gray-300',
  bronze: 'bg-orange-50 text-orange-700 border border-orange-300',
}
const STATUS_STYLES: Record<string, string> = {
  new:       'bg-blue-50 text-blue-700 border border-blue-200',
  contacted: 'bg-purple-50 text-purple-700 border border-purple-200',
  qualified: 'bg-indigo-50 text-indigo-700 border border-indigo-200',
  converted: 'bg-green-50 text-green-700 border border-green-200',
  lost:      'bg-red-50 text-red-600 border border-red-200',
}

function ScoreBadge({ score }: { score: number }) {
  const color = score >= 80 ? 'text-yellow-700 bg-yellow-50' : score >= 50 ? 'text-gray-600 bg-gray-50' : 'text-orange-600 bg-orange-50'
  return (
    <span className={`inline-flex items-center justify-center w-10 h-10 rounded-full font-bold text-sm ${color}`}>
      {score}
    </span>
  )
}

function RouteDisplay({ lead }: { lead: Lead }) {
  if (lead.route_display) return <span className="text-sm font-medium text-gray-900">{lead.route_display}</span>
  if (lead.origin_code && lead.destination_code)
    return (
      <span className="text-sm font-medium text-gray-900 flex items-center gap-1">
        {lead.origin_code}<Plane className="w-3 h-3 text-gray-400" />{lead.destination_code}
      </span>
    )
  return <span className="text-sm text-gray-400 italic">No route</span>
}

export function Leads() {
  const [leads, setLeads]           = useState<Lead[]>([])
  const [total, setTotal]           = useState(0)
  const [loading, setLoading]       = useState(true)
  const [usingMock, setUsingMock]   = useState(false)
  const [search, setSearch]         = useState('')
  const [statusFilter, setStatus]   = useState('')
  const [tierFilter, setTier]       = useState('')
  const [offset, setOffset]         = useState(0)
  const [updatingId, setUpdatingId] = useState<string | null>(null)
  const [selectedLeadId, setSelectedLeadId] = useState<string | null>(null)
  const LIMIT = 50

  const fetchLeads = useCallback(async () => {
    setLoading(true)
    try {
      const params: Record<string, string> = { limit: String(LIMIT), offset: String(offset) }
      if (search)       params.search = search
      if (statusFilter) params.status = statusFilter
      if (tierFilter)   params.tier = tierFilter
      const json = await getLeads(params)
      setLeads(json.data); setTotal(json.count); setUsingMock(false)
    } catch (err) {
      console.error('[leads] API error:', err)
      setLeads([]); setTotal(0); setUsingMock(false)
    } finally { setLoading(false) }
  }, [search, statusFilter, tierFilter, offset])

  useEffect(() => { fetchLeads() }, [fetchLeads])

  const handleStatusUpdate = async (leadId: string, newStatus: string) => {
    setUpdatingId(leadId)
    try {
      await updateLeadStatus(leadId, newStatus)
    } catch {
      // Silently fail — optimistic update still applies for mock mode
    } finally {
      // Optimistic update — works even in mock mode
      setLeads(prev => prev.map(l => l.id === leadId ? { ...l, status: newStatus as Lead['status'] } : l))
      setUpdatingId(null)
    }
  }

  const sortedLeads = [...(leads || [])].sort((a, b) => (b.score || 0) - (a.score || 0))

  return (
    <>
      <Header>
        <div className='ms-auto flex items-center space-x-4'>
          <ConnectionBanner />
          <ThemeSwitch />
          <ProfileDropdown />
        </div>
      </Header>
      <Main>
        <div className="space-y-5">
          {/* Header */}
          <div className="flex items-center justify-between">
            <div>
              <h1 className="text-2xl font-bold text-[#0B1829]">Leads</h1>
              <p className="text-sm text-gray-500 mt-0.5">{total} total{usingMock && ' · mock data'}</p>
            </div>
            <div className="flex items-center gap-2">
              <ExportLeadsButton leads={sortedLeads} />
              <button onClick={fetchLeads}
                className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-[#0B1829] bg-white border border-gray-200 rounded-lg hover:bg-gray-50 transition">
                <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />Refresh
              </button>
            </div>
          </div>

          {/* Filters */}
          <div className="flex flex-wrap gap-3">
            <div className="relative flex-1 min-w-[200px]">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <input type="text" placeholder="Search name, email, route..." value={search}
                onChange={e => setSearch(e.target.value)}
                className="w-full pl-9 pr-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-[#C9A54E]/40 focus:border-[#C9A54E]" />
            </div>
            <div className="relative">
              <Filter className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
              <select value={statusFilter} onChange={e => { setStatus(e.target.value); setOffset(0) }}
                className="pl-9 pr-8 py-2 text-sm border border-gray-200 rounded-lg appearance-none bg-white focus:outline-none focus:ring-2 focus:ring-[#C9A54E]/40">
                <option value="">All Status</option>
                <option value="new">New</option><option value="contacted">Contacted</option>
                <option value="qualified">Qualified</option><option value="converted">Converted</option>
                <option value="lost">Lost</option>
              </select>
              <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
            </div>
            <div className="relative">
              <select value={tierFilter} onChange={e => { setTier(e.target.value); setOffset(0) }}
                className="pl-3 pr-8 py-2 text-sm border border-gray-200 rounded-lg appearance-none bg-white focus:outline-none focus:ring-2 focus:ring-[#C9A54E]/40">
                <option value="">All Tiers</option>
                <option value="gold">Gold</option><option value="silver">Silver</option><option value="bronze">Bronze</option>
              </select>
              <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400 pointer-events-none" />
            </div>
          </div>

          {/* Table */}
          <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm">
            {loading ? (
              <div className="flex items-center justify-center h-48 text-gray-400 text-sm">Loading leads...</div>
            ) : leads.length === 0 ? (
              <div className="flex flex-col items-center justify-center h-48 text-gray-400">
                <Plane className="w-8 h-8 mb-2 opacity-30" /><p className="text-sm">No leads found</p>
              </div>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-gray-100 bg-gray-50">
                    {['Score', 'Contact', 'Route', 'Tier', 'Status', 'Departure', 'Action'].map(h => (
                      <th key={h} className="text-left px-4 py-3 font-medium text-gray-500 text-xs uppercase tracking-wide">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-50">
                  {sortedLeads.map(lead => (
                    <tr key={lead.id} className="hover:bg-gray-50 transition-colors cursor-pointer" onClick={() => setSelectedLeadId(lead.id)}>
                      <td className="px-4 py-3"><ScoreBadge score={lead.score} /></td>
                      <td className="px-4 py-3">
                        <div className="font-medium text-gray-900">
                          {lead.visitor_name ?? <span className="text-gray-400 italic text-xs">Anonymous</span>}
                        </div>
                        {lead.visitor_phone && <div className="flex items-center gap-1 text-xs text-gray-500 mt-0.5"><Phone className="w-3 h-3" />{lead.visitor_phone}</div>}
                        {lead.visitor_email && <div className="flex items-center gap-1 text-xs text-gray-400 mt-0.5"><Mail className="w-3 h-3" />{lead.visitor_email}</div>}
                      </td>
                      <td className="px-4 py-3">
                        <RouteDisplay lead={lead} />
                        <div className="text-xs text-gray-400 mt-0.5 capitalize">
                          {lead.trip_type.replace('_', '-')}{lead.passengers ? ` · ${lead.passengers} pax` : ''}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium capitalize ${TIER_STYLES[lead.tier]}`}>
                          {lead.tier}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`inline-flex px-2 py-0.5 rounded-full text-xs font-medium capitalize ${STATUS_STYLES[lead.status]}`}>
                          {lead.status}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-gray-600 text-xs">{lead.departure_date ?? <span className="text-gray-300">—</span>}</td>
                      <td className="px-4 py-3">
                        <select value={lead.status} disabled={updatingId === lead.id}
                          onChange={e => handleStatusUpdate(lead.id, e.target.value)}
                          className="text-xs border border-gray-200 rounded-md px-2 py-1 bg-white focus:outline-none focus:ring-1 focus:ring-[#C9A54E] disabled:opacity-50">
                          <option value="new">New</option><option value="contacted">Contacted</option>
                          <option value="qualified">Qualified</option><option value="converted">Converted</option>
                          <option value="lost">Lost</option>
                        </select>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Pagination */}
          {total > LIMIT && (
            <div className="flex items-center justify-between text-sm text-gray-500">
              <span>Showing {offset + 1}–{Math.min(offset + LIMIT, total)} of {total}</span>
              <div className="flex gap-2">
                <button onClick={() => setOffset(Math.max(0, offset - LIMIT))} disabled={offset === 0}
                  className="px-3 py-1.5 border border-gray-200 rounded-lg disabled:opacity-40 hover:bg-gray-50">Previous</button>
                <button onClick={() => setOffset(offset + LIMIT)} disabled={offset + LIMIT >= total}
                  className="px-3 py-1.5 border border-gray-200 rounded-lg disabled:opacity-40 hover:bg-gray-50">Next</button>
              </div>
            </div>
          )}
        </div>

        <LeadDetailDrawer
          leadId={selectedLeadId}
          onClose={() => setSelectedLeadId(null)}
        />
      </Main>
    </>
  )
}
