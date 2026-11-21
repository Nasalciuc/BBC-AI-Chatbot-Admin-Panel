import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { toast } from 'sonner'
import { getQaSample, getQaStats, setQaVerdict } from '@/lib/api'
import type { QaItem, QaVerdict } from '@/lib/types'
import { cn } from '@/lib/utils'
import { HeaderActions } from '@/components/header-actions'
import { PageHeader } from '@/components/layout/page-header'
import { VERDICT_LABEL, countsLine, reasonsLine, statsLine, yesterdayInNewYork } from './view'

const VERDICT_CLS: Record<QaVerdict, string> = {
  ok: 'bg-k-e3f6ea text-k-0a6b35', problem: 'bg-k-fdecea text-k-b3261e', discuss: 'bg-k-fff3e0 text-k-8a4b00',
}

function Item({ item, day }: { item: QaItem; day: string }) {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [note, setNote] = useState(item.note ?? '')
  const save = useMutation({
    mutationFn: (verdict: QaVerdict) => setQaVerdict(item.conversation_id, day, verdict, note.trim() || null),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['qa-sample', day] }); qc.invalidateQueries({ queryKey: ['qa-stats'] }) },
    onError: (e: Error) => toast.error(e.message),
  })
  return (
    <li className='flex flex-col gap-2 rounded-[20px] bg-k-f7f7f7 p-4'>
      <div className='flex flex-wrap items-center gap-2 text-sm'>
        <button type='button' className='font-bold text-k-5b45e0 hover:underline'
          onClick={() => navigate({ to: '/chats', search: { highlight: item.conversation_id } as never })}>
          {item.chat_number != null ? `#${item.chat_number}` : 'The chat'}
        </button>
        <span className='text-k-202020'>{item.agent_name || 'An agent'}</span>
        {item.verdict && (
          <span className={cn('rounded-full px-2 py-0.5 text-xs font-medium', VERDICT_CLS[item.verdict])}>
            {VERDICT_LABEL[item.verdict]}{item.verdict_by ? ` · ${item.verdict_by}` : ''}
          </span>
        )}
      </div>
      <span className='text-[13px] text-k-555555'>{reasonsLine(item)}</span>
      <label className='flex flex-col gap-1 text-xs text-k-555555'>
        Note
        <input value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)}
          className='h-9 rounded-[10px] border border-k-e3e2e2 bg-k-ffffff px-2.5 text-sm text-k-202020' />
      </label>
      <div className='flex flex-wrap gap-2'>
        {(['ok', 'problem', 'discuss'] as const).map((v) => (
          <button key={v} type='button' disabled={save.isPending} onClick={() => save.mutate(v)}
            className={cn('h-9 rounded-xl px-3.5 text-sm font-semibold', item.verdict === v ? 'bg-kb-202020 text-on-btn' : 'bg-k-ffffff text-k-202020 shadow-[inset_0_0_0_1px_var(--k-e3e2e2)]')}>
            {VERDICT_LABEL[v]}
          </button>
        ))}
      </div>
    </li>
  )
}

/** The morning QA sample: the chats picked from yesterday, why, a verdict on each; and each agent's week. */
export function QaPage() {
  const [day, setDay] = useState(yesterdayInNewYork)
  const sample = useQuery({ queryKey: ['qa-sample', day], queryFn: () => getQaSample(day) })
  const stats = useQuery({ queryKey: ['qa-stats'], queryFn: () => getQaStats(7) })
  return (
    <main id='content' className='flex flex-col gap-[18px] px-4 pt-6 pb-9 md:px-7 md:pt-7'>
      <PageHeader
        title='QA sample'
        subtitle='Every morning at 7 (New York): every chat that looks wrong, half of a new agent’s, and 15% of the rest. Nothing on an agent’s screen says a chat was picked.'
        actions={<HeaderActions className='ms-0' />}
      />
      <label className='flex items-center gap-2 text-sm text-k-555555'>
        Day
        <input type='date' value={day} onChange={(e) => e.target.value && setDay(e.target.value)}
          className='h-9 rounded-[10px] border border-k-e3e2e2 bg-k-ffffff px-2.5 text-sm text-k-202020' />
        {sample.data && <span className='text-k-202020'>{countsLine(sample.data.counts)}</span>}
      </label>
      {sample.isLoading ? (
        <p className='text-sm text-k-6b6b6b'>Loading…</p>
      ) : sample.isError ? (
        <p className='text-sm text-k-b3261e'>Couldn’t load the sample: {sample.error instanceof Error ? sample.error.message : 'unknown error'}.</p>
      ) : (sample.data?.items.length ?? 0) === 0 ? (
        <p className='text-sm text-k-6b6b6b'>No chat was picked that day.</p>
      ) : (
        <ul className='flex flex-col gap-2.5'>
          {sample.data!.items.map((i) => <Item key={i.conversation_id} item={i} day={day} />)}
        </ul>
      )}
      <section aria-label='The week per agent' className='flex flex-col gap-2'>
        <h2 className='text-[13px] font-bold tracking-[0.04em] text-k-202020 uppercase'>The last 7 days, per agent</h2>
        {stats.isError ? (
          <p className='text-sm text-k-b3261e'>Couldn’t load the week.</p>
        ) : (stats.data ?? []).length === 0 ? (
          <p className='text-sm text-k-6b6b6b'>Nothing picked in the last 7 days.</p>
        ) : (
          <ul className='flex flex-col gap-1.5'>
            {(stats.data ?? []).map((s) => (
              <li key={s.agent_id} className='flex justify-between gap-2 rounded-xl bg-k-f7f7f7 px-3.5 py-2.5 text-sm'>
                <span className='text-k-202020'>{s.agent_name || 'An agent'}</span>
                <span className='text-k-555555'>{statsLine(s)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  )
}
