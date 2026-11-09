import { useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearch } from '@tanstack/react-router'
import { Check, Search } from 'lucide-react'
import { toast } from 'sonner'
import { type ApiError, getLeadsView, getUsers, reviewLead } from '@/lib/api'
import type { LeadLevel, LeadRow, LeadsOrder, LeadsView } from '@/lib/types'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'
import { HeaderActions } from '@/components/header-actions'
import { PageHeader } from '@/components/layout/page-header'
import { LeadPanel } from './components/lead-panel'
import {
  LEVELS,
  TAB_LABEL,
  TAB_TEXT,
  TONE_DOT,
  TONE_TEXT,
  ageText,
  chatWithText,
  crmLine,
  leadsCsv,
  leadsTabsFor,
  navText,
  personLine,
  reviewCards,
  summaryCards,
  tripLine,
} from './view'

const LIMIT = 50
const ORDERS: { key: LeadsOrder; label: string }[] = [
  { key: 'oldest', label: 'Oldest first' },
  { key: 'closest', label: 'Closest to Ready' },
  { key: 'newest', label: 'Newest first' },
]

const selectCls =
  'h-10 rounded-xl border border-k-d9d9d9 bg-k-ffffff px-3 text-sm text-k-202020 focus-visible:outline-2 focus-visible:outline-k-6ea8ff'

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

/**
 * Leads: what reached the CRM and what didn't. Owner, admin and QA (the
 * route guard). The rows, the tabs and their counts come from one server
 * definition (GET /api/leads?view=…). QA reads it as a reviewer: To review
 * first, with notes and "Mark reviewed and next" in the panel.
 */
export function Leads() {
  const role = useAuthStore((s) => s.auth.user?.role ?? '')
  const meId = useAuthStore((s) => s.auth.user?.accountNo)
  const tabs = leadsTabsFor(role)
  const isQa = role === 'qa'
  const params = useSearch({ strict: false }) as { view?: LeadsView; level?: LeadLevel }
  const qc = useQueryClient()
  const canReview = ['owner', 'admin', 'supervisor', 'qa'].includes(role)
  const canSend = ['owner', 'admin', 'dev'].includes(role)
  const canLookUp = ['owner', 'admin', 'qa'].includes(role)

  const [view, setView] = useState<LeadsView>(params.view && tabs.includes(params.view) ? params.view : tabs[0])
  const [level, setLevel] = useState<LeadLevel | ''>(params.level ?? '')
  const [withWho, setWithWho] = useState<'' | 'person' | 'ai'>('')
  const [order, setOrder] = useState<LeadsOrder | null>(null)
  const [search, setSearch] = useState('')
  const [offset, setOffset] = useState(0)
  // The opened lead is kept even when a change moves it off this page
  // (a review takes it out of "To review"): the panel stays open.
  const [openSnap, setOpenSnap] = useState<LeadRow | null>(null)
  const [nowMs, setNowMs] = useState(() => Date.now())
  const q = useDebounced(search.trim(), 300)
  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 60_000)
    return () => clearInterval(t)
  }, [])
  // A new tab, filter or search starts from the first page.
  useEffect(() => setOffset(0), [view, level, withWho, order, q])

  const list = useQuery({
    queryKey: ['leads-view', view, level, withWho, order, q, offset],
    queryFn: () =>
      getLeadsView({ view, level: level || undefined, chat_with: withWho || undefined, order: order ?? undefined, search: q || undefined, limit: LIMIT, offset }),
    placeholderData: keepPreviousData,
  })
  // Reviewer names for "Reviewed by …" (owner/admin can list users; QA can't).
  const users = useQuery({
    queryKey: ['users', 'names'],
    queryFn: async () => (await getUsers()).data as unknown as { id: string; name: string | null; email: string }[],
    enabled: ['owner', 'admin', 'dev'].includes(role),
    staleTime: 5 * 60_000,
  })
  const userNames = useMemo(() => Object.fromEntries((users.data ?? []).map((u) => [u.id, u.name || u.email])), [users.data])

  const review = useMutation({
    mutationFn: (r: LeadRow) => reviewLead(r.id, !r.reviewed_by_qa),
    onSuccess: (_x, r) => {
      toast.success(r.reviewed_by_qa ? 'Review cleared.' : 'Marked as reviewed.')
      qc.invalidateQueries({ queryKey: ['leads-view'] })
    },
    onError: (e) => toast.error((e as ApiError)?.message ?? 'Couldn’t save the review. Try again.'),
  })

  const data = list.data
  const rows = data?.data ?? []
  const counts = data?.counts
  const shownOrder = order ?? data?.order ?? (view === 'to_review' ? 'oldest' : 'newest')
  const openRow = openSnap
  // QA goes from one lead to the next on this page of the list.
  const openIndex = openSnap ? rows.findIndex((r) => r.id === openSnap.id) : -1
  const nav =
    openSnap && openIndex >= 0 && data
      ? {
          text: navText(offset + openIndex, data.count, view),
          prev: openIndex > 0 ? () => setOpenSnap(rows[openIndex - 1]) : undefined,
          next: openIndex < rows.length - 1 ? () => setOpenSnap(rows[openIndex + 1]) : undefined,
        }
      : undefined
  const [exporting, setExporting] = useState(false)

  const exportCsv = async () => {
    setExporting(true)
    try {
      const all = await getLeadsView({ view, level: level || undefined, chat_with: withWho || undefined, order: shownOrder, search: q || undefined, limit: 1000, offset: 0 })
      const blob = new Blob([leadsCsv(all.data)], { type: 'text/csv;charset=utf-8' })
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `bbc-leads-${view}-${new Date().toISOString().slice(0, 10)}.csv`
      a.click()
      URL.revokeObjectURL(a.href)
      toast.success(`Exported ${all.data.length} ${all.data.length === 1 ? 'lead' : 'leads'}${all.count > all.data.length ? ` of ${all.count}` : ''}.`)
    } catch (e) {
      toast.error((e as ApiError)?.message ?? 'Couldn’t export. Try again.')
    } finally {
      setExporting(false)
    }
  }

  return (
    <main id='content' className='flex flex-col gap-4 px-4 pt-6 pb-9 md:px-7 md:pt-7'>
      <PageHeader
        title='Leads'
        subtitle={isQa ? 'Read the conversations and check how each lead was handled' : 'Every lead the chat collects, from first message to the CRM'}
        actions={
          <div className='flex items-center gap-2.5'>
            <button
              type='button'
              onClick={exportCsv}
              disabled={exporting || !data?.count}
              className='h-11 rounded-full border border-kl-202020 px-5 text-sm font-bold text-k-202020 disabled:opacity-50'
            >
              {exporting ? 'Exporting…' : 'Export CSV'}
            </button>
            <HeaderActions className='ms-0' />
          </div>
        }
      />

      <div role='tablist' aria-label='Leads' className='flex w-fit max-w-full flex-wrap gap-1 rounded-2xl bg-k-f2f2f2 p-1'>
        {tabs.map((key) => {
          const n = counts?.[key]
          return (
            <button
              key={key}
              type='button'
              role='tab'
              aria-selected={view === key}
              onClick={() => {
                setView(key)
                setOrder(null)
                setOpenSnap(null)
              }}
              className={cn(
                'flex h-10 items-center gap-2 rounded-xl px-4 text-[15px] focus-visible:outline-2 focus-visible:outline-k-6ea8ff',
                view === key ? 'bg-k-ffffff font-bold text-k-202020 shadow-sm' : 'text-k-444444'
              )}
            >
              {TAB_LABEL[key]}
              {n !== undefined && (
                <span
                  className={cn(
                    'rounded-full px-1.5 text-xs font-bold',
                    key === 'not_in_crm' && n > 0 ? 'bg-k-fdecea text-k-b3261e' : 'bg-k-e9e9e9 text-k-444444'
                  )}
                >
                  {n}
                </span>
              )}
            </button>
          )
        })}
      </div>

      <div className='flex flex-col gap-3 lg:flex-row lg:items-center'>
        <p className='m-0 flex-1 text-sm leading-[1.45] text-k-444444'>{TAB_TEXT[view]}</p>
        <div className='flex flex-wrap items-center gap-2'>
          <label className='relative'>
            <span className='sr-only'>Search leads</span>
            <Search aria-hidden='true' className='pointer-events-none absolute start-3 top-1/2 size-4 -translate-y-1/2 text-k-6b6b6b' />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder='Name, e-mail, route or #4810'
              className='h-10 w-full rounded-xl bg-k-f2f2f2 ps-9 pe-3 text-sm text-k-202020 placeholder:text-k-6b6b6b sm:w-[260px]'
            />
          </label>
          <label className='flex items-center gap-1.5 text-[13px] text-k-555555'>
            Level
            <select value={level} onChange={(e) => setLevel(e.target.value as LeadLevel | '')} className={selectCls}>
              <option value=''>All</option>
              {(Object.keys(LEVELS) as LeadLevel[]).map((k) => (
                <option key={k} value={k}>
                  {LEVELS[k].name}
                </option>
              ))}
            </select>
          </label>
          <label className='flex items-center gap-1.5 text-[13px] text-k-555555'>
            Chat with
            <select value={withWho} onChange={(e) => setWithWho(e.target.value as '' | 'person' | 'ai')} className={selectCls}>
              <option value=''>Anyone</option>
              <option value='person'>A person</option>
              <option value='ai'>Only the AI</option>
            </select>
          </label>
        </div>
      </div>

      {isQa && (view === 'to_review' || view === 'reviewed') && data?.review && (
        <div className='grid grid-cols-1 gap-3 sm:grid-cols-3'>
          {reviewCards(data.review).map((c, i) => (
            <div key={c.text} className={cn('flex items-baseline gap-2 rounded-[18px] px-4 py-3', i === 0 ? 'bg-k-fdf6e3' : 'bg-k-f7f7f7')}>
              <span className='text-2xl font-bold text-k-202020'>{c.n}</span>
              <span className='text-[13px] leading-[1.3] text-k-444444'>{c.text}</span>
            </div>
          ))}
        </div>
      )}

      {view === 'not_in_crm' && data?.summary && summaryCards(data.summary).length > 0 && (
        <div className='grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-5'>
          {summaryCards(data.summary).map((c) => (
            <div key={c.text} className='flex flex-col gap-1 rounded-[18px] bg-k-f7f7f7 px-4 py-3'>
              <span className={cn('text-2xl font-bold', c.bad ? 'text-k-b3261e' : 'text-k-202020')}>{c.n}</span>
              <span className='text-[13px] leading-[1.3] text-k-444444'>{c.text}</span>
            </div>
          ))}
        </div>
      )}

      <div className='flex flex-wrap items-center gap-2'>
        <span className='text-[11px] font-bold tracking-[0.05em] text-k-555555 uppercase'>Order</span>
        <div role='radiogroup' aria-label='Order' className='flex gap-1 rounded-2xl bg-k-f2f2f2 p-1'>
          {ORDERS.map((o) => (
            <button
              key={o.key}
              type='button'
              role='radio'
              aria-checked={shownOrder === o.key}
              onClick={() => setOrder(o.key)}
              className={cn(
                'h-8 rounded-xl px-3 text-[13px] focus-visible:outline-2 focus-visible:outline-k-6ea8ff',
                shownOrder === o.key ? 'bg-k-ffffff font-bold text-k-202020 shadow-sm' : 'text-k-444444'
              )}
            >
              {o.label}
            </button>
          ))}
        </div>
      </div>

      {list.isError ? (
        <div role='alert' className='flex flex-wrap items-center gap-3 rounded-2xl bg-k-fff3e0 px-4 py-3 text-sm text-k-8a4b00'>
          <span className='flex-1'>
            <strong>Couldn’t load the leads.</strong> The list isn’t shown rather than shown empty.
          </span>
          <button type='button' onClick={() => list.refetch()} className='h-9 rounded-full bg-kb-202020 px-4 text-[13px] font-bold text-on-btn'>
            Try again
          </button>
        </div>
      ) : list.isLoading ? (
        <div className='h-64 animate-pulse rounded-[20px] bg-k-f7f7f7' aria-busy='true' />
      ) : rows.length === 0 ? (
        <div className='rounded-[20px] px-5 py-10 text-center text-sm text-k-555555 ring-1 ring-k-ececec'>
          {q || level || withWho
            ? 'No lead matches. Clear the search or the filters.'
            : view === 'not_in_crm'
              ? 'Every lead with a phone is in the CRM.'
              : view === 'to_review'
                ? 'No ready lead is waiting for review.'
                : view === 'reviewed'
                  ? 'No lead has been reviewed yet.'
                  : 'No leads yet.'}
        </div>
      ) : (
        <div className={cn('overflow-x-auto rounded-[20px] ring-1 ring-k-ececec', list.isFetching && 'opacity-70')}>
          <table className='w-full min-w-[980px] border-collapse'>
            <thead>
              <tr className='bg-k-f7f7f7'>
                {['Level', 'Person', 'Trip', 'Age', 'Chat with', 'CRM', ...(canReview ? ['QA'] : [])].map((h) => (
                  <th key={h} className='px-4 py-3 text-left text-[11px] font-bold tracking-[0.05em] text-k-555555 uppercase'>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const lv = LEVELS[r.level]
                const trip = tripLine(r)
                const crm = crmLine(r)
                const who = chatWithText(r)
                return (
                  <tr
                    key={r.id}
                    tabIndex={0}
                    onClick={() => setOpenSnap(r)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        setOpenSnap(r)
                      }
                    }}
                    className='cursor-pointer border-t border-k-ececec hover:bg-row-hover focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-k-6ea8ff'
                  >
                    <td className='px-4 py-3 align-top'>
                      <span className={cn('rounded-full px-2.5 py-1 text-xs font-bold', lv.chipCls)}>{lv.chip}</span>
                    </td>
                    <td className='max-w-[240px] px-4 py-3 align-top'>
                      <strong className='block truncate text-[15px] text-k-202020'>{r.visitor_name || 'No name'}</strong>
                      <span className='block truncate text-xs text-k-555555'>{personLine(r) || '—'}</span>
                    </td>
                    <td className='px-4 py-3 align-top'>
                      <strong className='block text-[15px] text-k-202020'>{trip.route}</strong>
                      <span className='block text-xs text-k-555555'>{trip.detail}</span>
                    </td>
                    <td className='px-4 py-3 align-top text-sm whitespace-nowrap text-k-202020'>{ageText(r.created_at, nowMs)}</td>
                    <td className={cn('px-4 py-3 align-top text-sm', who.ai ? 'text-k-5b45e0' : 'text-k-202020')}>{who.text}</td>
                    <td className='max-w-[260px] px-4 py-3 align-top'>
                      <span className={cn('flex items-start gap-2 text-sm', TONE_TEXT[crm.tone])}>
                        <span aria-hidden='true' className={cn('mt-1.5 size-2 flex-none rounded-full', TONE_DOT[crm.tone])} />
                        <span className='line-clamp-2'>{crm.text}</span>
                      </span>
                    </td>
                    {canReview && (
                      <td className='px-4 py-3 align-top'>
                        <button
                          type='button'
                          aria-pressed={!!r.reviewed_by_qa}
                          aria-label={r.reviewed_by_qa ? `Reviewed: clear the review for ${r.visitor_name ?? 'this lead'}` : `Mark ${r.visitor_name ?? 'this lead'} as reviewed`}
                          disabled={review.isPending}
                          onClick={(e) => {
                            e.stopPropagation()
                            review.mutate(r)
                          }}
                          className={cn(
                            'flex size-8 items-center justify-center rounded-full focus-visible:outline-2 focus-visible:outline-k-6ea8ff',
                            r.reviewed_by_qa ? 'bg-k-e3f6ea text-k-0a6b35' : 'text-k-9c9c9c hover:bg-k-f2f2f2'
                          )}
                        >
                          {r.reviewed_by_qa ? <Check className='size-4' /> : <span aria-hidden='true'>·</span>}
                        </button>
                      </td>
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {data && data.count > LIMIT && (
        <div className='flex items-center justify-between gap-3 text-sm text-k-555555'>
          <span>
            Showing {offset + 1}–{Math.min(offset + LIMIT, data.count)} of {data.count}
          </span>
          <div className='flex gap-2'>
            <button
              type='button'
              disabled={offset === 0}
              onClick={() => setOffset((o) => Math.max(0, o - LIMIT))}
              className='h-9 rounded-full border border-k-c9c9c9 px-4 font-bold text-k-202020 disabled:opacity-40'
            >
              Previous
            </button>
            <button
              type='button'
              disabled={offset + LIMIT >= data.count}
              onClick={() => setOffset((o) => o + LIMIT)}
              className='h-9 rounded-full border border-k-c9c9c9 px-4 font-bold text-k-202020 disabled:opacity-40'
            >
              Next
            </button>
          </div>
        </div>
      )}

      <LeadPanel
        row={openRow}
        onClose={() => setOpenSnap(null)}
        onPatched={(leadId, patch) => setOpenSnap((s) => (s && s.id === leadId ? { ...s, ...patch } : s))}
        canReview={canReview}
        canSend={canSend}
        canLookUp={canLookUp}
        userNames={userNames}
        nowMs={nowMs}
        meId={meId}
        nav={canReview ? nav : undefined}
        reviewFirst={isQa}
      />
    </main>
  )
}
