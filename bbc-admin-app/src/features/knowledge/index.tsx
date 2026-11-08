import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, Copy, Search } from 'lucide-react'
import { toast } from 'sonner'
import { getKBCategories, getKBEntries } from '@/lib/api'
import type { KBEntry } from '@/lib/types'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'
import { PageHeader } from '@/components/layout/page-header'
import { HeaderActions } from '@/components/header-actions'

const SUBTITLE: Record<string, string> = {
  sales: 'Answers about booking, payment, prices and routes. Copy one into a chat when it fits the client.',
  support: 'Answers about baggage, changes, cancellations and travel. Copy one into a chat when it fits the client.',
}

function updatedOn(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric' })
}

/**
 * Knowledge, for agents: the answers in the knowledge base for their tunnel,
 * to read and copy. Read-only by design — agents can't change answers here
 * (the editor is the Assistant page, for owner and admins).
 */
export function Knowledge() {
  const scope = useAuthStore((s) => s.auth.user?.tunnelScope) || 'sales'
  const tunnel = scope === 'support' ? 'support' : scope === 'all' ? '' : 'sales'
  const [query, setQuery] = useState('')
  const [category, setCategory] = useState<string>('all')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [showDetail, setShowDetail] = useState(false)

  const { data: catData } = useQuery({ queryKey: ['kb-categories'], queryFn: getKBCategories, staleTime: 5 * 60_000 })
  const { data: entryData, isLoading, isError, refetch } = useQuery({
    queryKey: ['kb-entries', 'read', tunnel],
    queryFn: () => getKBEntries({ ...(tunnel ? { tunnel } : {}), is_active: 'true', limit: '500' }),
    staleTime: 60_000,
  })

  const categories = useMemo(
    () => (catData?.data ?? []).filter((c) => !tunnel || c.tunnel === tunnel),
    [catData, tunnel]
  )
  const catName = useMemo(() => Object.fromEntries(categories.map((c) => [c.id, c.name])), [categories])
  const entries: KBEntry[] = useMemo(() => (entryData?.data ?? []).filter((e) => e.is_active), [entryData])

  const q = query.trim().toLowerCase()
  const shown = entries.filter(
    (e) =>
      (category === 'all' || e.category_id === category) &&
      (!q || e.title.toLowerCase().includes(q) || e.content.toLowerCase().includes(q))
  )
  const selected = shown.find((e) => e.id === selectedId) ?? shown[0] ?? null
  const counts = (id: string) => entries.filter((e) => e.category_id === id).length

  const copy = async (e: KBEntry) => {
    try {
      await navigator.clipboard.writeText(e.content)
      toast.success('Copied. Paste it into the chat.')
    } catch {
      toast.error('Couldn’t copy. Select the text and copy it yourself.')
    }
  }

  const chip = (on: boolean) =>
    cn(
      'flex h-10 flex-none items-center gap-1.5 rounded-full px-3.5 text-[13px] font-bold transition-colors focus-visible:outline-2 focus-visible:outline-k-6ea8ff',
      on ? 'bg-kb-202020 text-on-btn' : 'border border-k-e3e2e2 bg-k-ffffff text-k-202020 hover:bg-k-f7f7f7'
    )

  return (
    <main id='content' className='flex flex-col gap-[18px] px-4 pt-6 pb-8 md:px-7 md:pt-7'>
      <PageHeader title='Knowledge' subtitle={SUBTITLE[tunnel] ?? SUBTITLE.sales} actions={<HeaderActions />} menuButton={false} />

      <div className='flex flex-wrap items-center gap-2.5'>
        <label className='relative flex w-full max-w-[380px] flex-1'>
          <span className='sr-only'>Search answers</span>
          <Search aria-hidden='true' className='absolute top-1/2 left-4 size-4 -translate-y-1/2 text-k-6b6b6b' />
          <input
            type='search'
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder='Search answers, e.g. payment or London'
            className='h-11 w-full rounded-full border-none bg-k-ffffff ps-11 pe-4 text-[15px] text-k-202020 shadow-[inset_0_0_0_1px_var(--k-e3e2e2)] placeholder:text-k-8a8a8a focus-visible:outline-2 focus-visible:outline-k-6ea8ff'
          />
        </label>
        <div role='tablist' aria-label='Topics' className='flex flex-wrap gap-2'>
          <button type='button' role='tab' aria-selected={category === 'all'} className={chip(category === 'all')} onClick={() => setCategory('all')}>
            All <span className='font-normal opacity-70'>{entries.length}</span>
          </button>
          {categories.map((c) => (
            <button key={c.id} type='button' role='tab' aria-selected={category === c.id} className={chip(category === c.id)} onClick={() => setCategory(c.id)}>
              {c.name} <span className='font-normal opacity-70'>{counts(c.id)}</span>
            </button>
          ))}
        </div>
      </div>

      {isError ? (
        <div role='alert' className='flex flex-wrap items-center gap-3 rounded-2xl bg-k-fff3e0 px-4 py-3 text-sm text-k-8a4b00'>
          <span className='flex-1'>
            <strong>Couldn’t load the answers.</strong> The server didn’t answer.
          </span>
          <button type='button' onClick={() => refetch()} className='h-9 rounded-full bg-kb-202020 px-4 text-[13px] font-bold text-on-btn'>
            Try again
          </button>
        </div>
      ) : (
        <div className='flex min-h-0 gap-6'>
          <section aria-label='Answers' className={cn('flex w-full flex-col gap-2 md:w-[400px] md:flex-none', showDetail && 'max-md:hidden')}>
            <span className='px-1.5 text-xs text-k-6b6b6b'>
              {isLoading ? 'Loading…' : `${shown.length} ${shown.length === 1 ? 'answer' : 'answers'}`}
            </span>
            {!isLoading && shown.length === 0 && (
              <div className='rounded-2xl bg-k-f7f7f7 p-5 text-sm leading-normal text-k-555555'>
                {q ? `Nothing matches “${query.trim()}”.` : 'No answers here yet.'}
              </div>
            )}
            {shown.map((e) => {
              const on = selected?.id === e.id
              return (
                <button
                  key={e.id}
                  type='button'
                  aria-pressed={on}
                  onClick={() => {
                    setSelectedId(e.id)
                    setShowDetail(true)
                  }}
                  className={cn(
                    'flex flex-col gap-1.5 rounded-2xl px-4 py-3.5 text-start transition-colors focus-visible:outline-2 focus-visible:outline-k-6ea8ff',
                    on ? 'bg-k-ffffff shadow-[inset_0_0_0_2px_var(--kl-202020)]' : 'bg-k-f7f7f7 hover:bg-k-f2f2f2'
                  )}
                >
                  <span className='flex items-baseline gap-2'>
                    <strong className='min-w-0 flex-1 truncate text-[15px] text-k-202020'>{e.title}</strong>
                    <span className='flex-none text-xs text-k-6b6b6b'>{catName[e.category_id] ?? ''}</span>
                  </span>
                  <span className='line-clamp-2 text-[13px] leading-[1.45] text-k-444444'>{e.content}</span>
                </button>
              )
            })}
          </section>

          {selected && (
            <article
              aria-label={selected.title}
              className={cn(
                'flex h-fit min-w-0 flex-1 flex-col gap-4 rounded-[20px] bg-k-ffffff p-6 shadow-[0_0_0_1px_var(--k-ececec)] md:p-8',
                !showDetail && 'max-md:hidden'
              )}
            >
              <button type='button' onClick={() => setShowDetail(false)} className='flex items-center gap-1.5 self-start text-sm font-bold text-k-5b45e0 md:hidden'>
                <ArrowLeft aria-hidden='true' className='size-4' /> All answers
              </button>
              <span className='text-[11px] font-bold tracking-[0.05em] text-k-6b6b6b uppercase'>{catName[selected.category_id] ?? ''}</span>
              <h2 className='m-0 text-[24px] font-bold text-k-202020 md:text-[28px]'>{selected.title}</h2>
              <span className='text-[13px] text-k-6b6b6b'>Updated {updatedOn(selected.updated_at)}</span>
              <p className='m-0 text-[17px] leading-[1.65] whitespace-pre-wrap text-k-202020'>{selected.content}</p>
              <div className='flex flex-wrap items-center gap-3'>
                <button
                  type='button'
                  onClick={() => copy(selected)}
                  className='flex h-11 items-center gap-2 rounded-full bg-kb-202020 px-5 text-[15px] font-bold text-on-btn focus-visible:outline-2 focus-visible:outline-k-6ea8ff'
                >
                  <Copy aria-hidden='true' className='size-4' /> Copy answer
                </button>
                <span className='text-[13px] text-k-6b6b6b'>Copies the text above, nothing else.</span>
              </div>
              <div className='h-px bg-k-f0f0f0' />
              <p className='m-0 text-[13px] leading-normal text-k-555555'>
                Something wrong or out of date? Tell your supervisor. You can read and copy answers here, not change them.
              </p>
            </article>
          )}
        </div>
      )}
    </main>
  )
}
