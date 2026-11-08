import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { ChevronDown, ChevronUp, SquareCheck } from 'lucide-react'
import { toast } from 'sonner'
import { type ApiError, updateTask } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useTaskAlerts } from '@/features/tasks/use-task-alerts'
import { bandLine, bandRows, clockIn, localZone, span } from '@/features/tasks/view'

/**
 * In the CRM dock: your follow-ups that are late or due within the hour, over
 * the chat list (the rest waits in Tasks). Shows only when there is one; late
 * ones turn it red. Done here is the same Done as in Tasks.
 */
export function TasksBand() {
  const qc = useQueryClient()
  const { data, dataUpdatedAt } = useTaskAlerts()
  const [open, setOpen] = useState(true)
  const done = useMutation({
    mutationFn: (id: string) => updateTask(id, { done: true }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['notifications'] })
      qc.invalidateQueries({ queryKey: ['tasks'] })
      qc.invalidateQueries({ queryKey: ['chat-tasks'] })
      toast.success('Done.')
    },
    onError: (e) => toast.error((e as ApiError)?.message ?? 'Couldn’t save the task. Try again.'),
  })
  const rows = bandRows(data?.mine ?? [], dataUpdatedAt)
  const tz = localZone()
  const line = bandLine(rows, tz)
  if (!line) return null
  return (
    <section aria-label='Your follow-ups' className={cn('border-b', line.late ? 'border-k-f0cdc8 bg-k-fde8e6' : 'border-k-ececec bg-k-f7f7f7')}>
      <button type='button' aria-expanded={open} onClick={() => setOpen((v) => !v)} className='flex w-full items-center gap-2.5 px-3.5 py-2.5 text-start'>
        <span aria-hidden='true' className={cn('flex size-6 flex-none items-center justify-center rounded-md', line.late ? 'bg-k-d93025 text-k-ffffff' : 'bg-k-202020 text-k-ffffff')}>
          <SquareCheck className='size-3.5' />
        </span>
        <span className='min-w-0 flex-1 text-[13px] text-k-202020'>
          <strong>{line.head}</strong> {line.text}
        </span>
        {open ? <ChevronUp aria-hidden='true' className='size-4 text-k-555555' /> : <ChevronDown aria-hidden='true' className='size-4 text-k-555555' />}
      </button>
      {open && (
        <ul className='m-0 flex list-none flex-col p-0'>
          {rows.map((r) => {
            const at = r.due_at ? Date.parse(r.due_at) : null
            const late = r.state === 'late'
            return (
              <li key={r.id} className='flex items-start gap-2.5 border-t border-k-ececec/70 px-3.5 py-2.5'>
                <button
                  type='button'
                  disabled={done.isPending}
                  onClick={() => done.mutate(r.id)}
                  aria-label={`Mark done: ${r.title}`}
                  className='mt-0.5 size-[18px] flex-none rounded-full border-2 border-k-9c9c9c hover:border-k-0a6b35 disabled:opacity-50'
                />
                <span className='flex min-w-0 flex-1 flex-col'>
                  <strong className='truncate text-[13px] text-k-202020'>{r.title}</strong>
                  <span className='truncate text-xs text-k-6b6b6b'>{[r.client, r.route].filter(Boolean).join(' · ')}</span>
                </span>
                {at !== null && (
                  <span className={cn('flex flex-none flex-col items-end text-xs', late ? 'text-k-b3261e' : 'text-k-555555')}>
                    <strong className='text-[13px]'>{clockIn(at, tz)}</strong>
                    {late ? `${span(dataUpdatedAt - at)} late` : `in ${span(at - dataUpdatedAt)}`}
                  </span>
                )}
              </li>
            )
          })}
          <li className='border-t border-k-ececec/70 px-3.5 py-2'>
            <Link to='/tasks' className='text-[13px] font-bold text-k-5b45e0 hover:underline'>
              All your tasks
            </Link>
          </li>
        </ul>
      )}
    </section>
  )
}
