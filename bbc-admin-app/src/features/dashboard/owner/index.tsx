import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { getOwnerDashboard } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useIsMobile } from '@/hooks/use-mobile'
import { usePanelModeStore } from '@/stores/panel-mode-store'
import { HeaderActions } from '@/components/header-actions'
import { PageHeader } from '@/components/layout/page-header'
import { minutesLabel } from '@/features/chats/view'
import { Agents } from './agents'
import { Conversion } from './conversion'
import { Overview } from './overview'
import { Quality } from './quality'
import { rangeText, sitesText, type Range } from './view'

type Tab = 'overview' | 'conversion' | 'agents' | 'quality'
const TABS: { key: Tab; label: string }[] = [
  { key: 'overview', label: 'Overview' },
  { key: 'conversion', label: 'Conversion' },
  { key: 'agents', label: 'Agents' },
  { key: 'quality', label: 'Quality' },
]
const RANGES: { days: Range; label: string }[] = [
  { days: 1, label: 'Today' },
  { days: 7, label: '7 days' },
  { days: 30, label: '30 days' },
]

function initialTab(): Tab {
  const t = new URLSearchParams(window.location.search).get('tab')
  return TABS.some((x) => x.key === t) ? (t as Tab) : 'overview'
}

function localToday(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/**
 * The owner's Dashboard: the whole business, read-only. Owner, admin, dev.
 * Every number comes from GET /api/dashboard/owner; every sentence from
 * ./view.ts. On a phone only the Overview is shown.
 */
export function OwnerDashboard() {
  const dormant = usePanelModeStore((s) => s.dormant)
  const navigate = useNavigate()
  const isMobile = useIsMobile()
  const [range, setRange] = useState<Range>(7)
  const [tab, setTab] = useState<Tab>(initialTab)
  const [nowMs, setNowMs] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 30_000)
    return () => clearInterval(t)
  }, [])

  const { data, isLoading, isError, refetch, dataUpdatedAt } = useQuery({
    queryKey: ['owner-dashboard', range],
    queryFn: () => getOwnerDashboard(range),
    refetchInterval: dormant ? false : 60_000,
  })

  const go = (to: string) => {
    if (to === 'review') {
      navigate({ to: '/leads', search: { view: 'to_review' } as never })
      return
    }
    const next: Tab = to === 'agents' || to === 'hour' || to === 'agent-table' ? 'agents' : to === 'quality' ? 'quality' : to === 'conversion' ? 'conversion' : 'overview'
    setTab(next)
    if (to === 'hour') setTimeout(() => document.getElementById('by-hour')?.scrollIntoView({ behavior: 'smooth' }), 50)
    else window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const updated = dataUpdatedAt ? Math.max(0, Math.floor((nowMs - dataUpdatedAt) / 60_000)) : null
  const shownTab: Tab = isMobile ? 'overview' : tab

  return (
    <main id='content' className='flex flex-col gap-[18px] px-4 pt-6 pb-9 md:px-7 md:pt-7'>
      <PageHeader
        title='Dashboard'
        subtitle={
          data ? (
            <>
              {sitesText(data.sites)} · all chats · {rangeText(data)}
              {updated !== null && ` · updated ${updated < 1 ? 'just now' : `${minutesLabel(updated)} ago`}`}
            </>
          ) : (
            'All chats'
          )
        }
        actions={
          <div className='flex w-full flex-wrap items-center gap-2.5 md:w-auto'>
            <div role='tablist' aria-label='Period' className='flex flex-1 gap-1 rounded-2xl bg-k-ececec p-1 md:flex-none'>
              {RANGES.map((r) => (
                <button
                  key={r.days}
                  type='button'
                  role='tab'
                  aria-selected={range === r.days}
                  onClick={() => setRange(r.days)}
                  className={cn(
                    'h-9 flex-1 rounded-xl px-4 text-sm focus-visible:outline-2 focus-visible:outline-k-6ea8ff md:flex-none',
                    range === r.days ? 'bg-k-ffffff font-bold text-k-202020 shadow-sm' : 'text-k-202020'
                  )}
                >
                  {r.label}
                </button>
              ))}
            </div>
            <HeaderActions className='ms-0' />
          </div>
        }
      />

      {!isMobile && (
        <div role='tablist' aria-label='Dashboard' className='flex gap-1 overflow-x-auto border-b border-k-ececec'>
          {TABS.map((t) => (
            <button
              key={t.key}
              type='button'
              role='tab'
              aria-selected={tab === t.key}
              onClick={() => setTab(t.key)}
              className={cn(
                '-mb-px h-11 border-b-2 px-3.5 text-[15px] whitespace-nowrap focus-visible:outline-2 focus-visible:outline-k-6ea8ff',
                tab === t.key ? 'border-k-202020 font-bold text-k-202020' : 'border-transparent text-k-555555 hover:text-k-202020'
              )}
            >
              {t.label}
            </button>
          ))}
        </div>
      )}

      {isError ? (
        <div role='alert' className='flex flex-wrap items-center gap-3 rounded-2xl bg-k-fff3e0 px-4 py-3 text-sm text-k-8a4b00'>
          <span className='flex-1'>
            <strong>Couldn’t load the dashboard.</strong> The numbers aren’t shown rather than shown wrong.
          </span>
          <button type='button' onClick={() => refetch()} className='h-9 rounded-full bg-kb-202020 px-4 text-[13px] font-bold text-on-btn'>
            Try again
          </button>
        </div>
      ) : isLoading || !data ? (
        <div className='flex flex-col gap-3.5' aria-busy='true'>
          <div className='h-14 animate-pulse rounded-[18px] bg-k-f3f0ff' />
          <div className='grid grid-cols-1 gap-3.5 sm:grid-cols-2 xl:grid-cols-4'>
            {[0, 1, 2, 3].map((i) => (
              <div key={i} className='h-44 animate-pulse rounded-[20px] bg-k-f7f7f7' />
            ))}
          </div>
        </div>
      ) : shownTab === 'conversion' ? (
        <Conversion d={data} />
      ) : shownTab === 'agents' ? (
        <Agents d={data} go={go} />
      ) : shownTab === 'quality' ? (
        <Quality d={data} go={go} />
      ) : (
        <Overview d={data} go={go} today={localToday()} />
      )}

      {isMobile && data && (
        <p className='m-0 rounded-2xl bg-k-f2f2f2 px-4 py-3 text-[13px] text-k-555555'>Conversion, Agents and Quality open on a computer.</p>
      )}
    </main>
  )
}
