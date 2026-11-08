import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { getOwnerDashboard, getTeamDashboard } from '@/lib/api'
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
import { TAB_LABEL, TABS_FOR, phoneNote, rangeText, scopeText, type DashTab as Tab, type DashVariant, type Range } from './view'

const RANGES: { days: Range; label: string }[] = [
  { days: 1, label: 'Today' },
  { days: 7, label: '7 days' },
  { days: 30, label: '30 days' },
]

function initialTab(tabs: Tab[]): Tab {
  const t = new URLSearchParams(window.location.search).get('tab')
  return tabs.includes(t as Tab) ? (t as Tab) : tabs[0]
}

function localToday(): string {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/**
 * The Dashboard, read-only, per role:
 *   owner (owner, admin, dev) — the whole business, GET /api/dashboard/owner;
 *   team (supervisor, project manager) — their tunnel, Overview and Agents;
 *   qa — all chats, Quality first, no AI cost and no sources.
 * The team and QA numbers come from GET /api/dashboard/team. Every sentence
 * comes from ./view.ts. On a phone only the first tab is shown.
 */
export function OwnerDashboard({ variant = 'owner' }: { variant?: DashVariant }) {
  const dormant = usePanelModeStore((s) => s.dormant)
  const navigate = useNavigate()
  const isMobile = useIsMobile()
  const tabs = TABS_FOR[variant]
  const [range, setRange] = useState<Range>(7)
  const [tab, setTab] = useState<Tab>(() => initialTab(tabs))
  const [nowMs, setNowMs] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 30_000)
    return () => clearInterval(t)
  }, [])

  const { data: raw, isLoading, isError, refetch, dataUpdatedAt } = useQuery({
    queryKey: ['owner-dashboard', variant, range],
    queryFn: () => (variant === 'owner' ? getOwnerDashboard(range) : getTeamDashboard(range)),
    refetchInterval: dormant ? false : 60_000,
  })
  // A supervisor's page is about agents: the review queue is QA's.
  const data =
    raw && variant === 'team' ? { ...raw, attention: raw.attention.filter((a) => a.kind === 'agent' || a.kind === 'hour') } : raw

  const go = (to: string) => {
    if (to === 'review') {
      navigate({ to: '/leads', search: { view: 'to_review' } as never })
      return
    }
    const want: Tab = to === 'agents' || to === 'hour' || to === 'agent-table' ? 'agents' : to === 'quality' ? 'quality' : to === 'conversion' ? 'conversion' : 'overview'
    const next = tabs.includes(want) ? want : tabs[0]
    setTab(next)
    const anchor = to === 'hour' ? 'by-hour' : to === 'agent-table' ? 'by-agent' : null
    if (anchor) setTimeout(() => document.getElementById(anchor)?.scrollIntoView({ behavior: 'smooth' }), 50)
    else window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const updated = dataUpdatedAt ? Math.max(0, Math.floor((nowMs - dataUpdatedAt) / 60_000)) : null
  const shownTab: Tab = isMobile ? tabs[0] : tabs.includes(tab) ? tab : tabs[0]

  return (
    <main id='content' className='flex flex-col gap-[18px] px-4 pt-6 pb-9 md:px-7 md:pt-7'>
      <PageHeader
        title='Dashboard'
        subtitle={
          data ? (
            <>
              {scopeText(data, variant)} · {rangeText(data)}
              {updated !== null && ` · updated ${updated < 1 ? 'just now' : `${minutesLabel(updated)} ago`}`}
            </>
          ) : variant === 'owner' ? (
            'All chats'
          ) : (
            ''
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
          {tabs.map((t) => (
            <button
              key={t}
              type='button'
              role='tab'
              aria-selected={shownTab === t}
              onClick={() => setTab(t)}
              className={cn(
                '-mb-px h-11 border-b-2 px-3.5 text-[15px] whitespace-nowrap focus-visible:outline-2 focus-visible:outline-k-6ea8ff',
                shownTab === t ? 'border-k-202020 font-bold text-k-202020' : 'border-transparent text-k-555555 hover:text-k-202020'
              )}
            >
              {TAB_LABEL[t]}
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
        <Agents d={data} go={go} canSeeTeam={variant !== 'qa'} />
      ) : shownTab === 'quality' ? (
        <Quality d={data} go={go} showCrm={variant === 'owner'} />
      ) : (
        <Overview d={data} go={go} tabs={!isMobile} today={localToday()} team={variant === 'team'} />
      )}

      {isMobile && data && phoneNote(variant) && (
        <p className='m-0 rounded-2xl bg-k-f2f2f2 px-4 py-3 text-[13px] text-k-555555'>{phoneNote(variant)}</p>
      )}
    </main>
  )
}
