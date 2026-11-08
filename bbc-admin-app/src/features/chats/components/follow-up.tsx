import { Clock, Mail, Phone, SquareCheck } from 'lucide-react'
import { Link } from '@tanstack/react-router'
import type { TaskRow } from '@/lib/types'
import { cn } from '@/lib/utils'
import { caughtLine, lateBanner } from '@/features/tasks/view'

const ICON = { email: Mail, call: Phone, todo: SquareCheck } as const

/** Under the agent's message: "Follow-up caught: e-mail by 15:07 · Edit · Not a promise" (red once late). */
export function FollowUpLine({
  task,
  nowMs,
  tz,
  busy,
  onNotAPromise,
}: {
  task: TaskRow
  nowMs: number
  tz: string
  busy: boolean
  onNotAPromise: (() => void) | null
}) {
  const line = caughtLine(task, nowMs, tz)
  const Icon = ICON[task.kind]
  return (
    <span
      className={cn(
        'mt-1 flex w-fit max-w-full flex-wrap items-center gap-x-1.5 gap-y-0.5 rounded-[10px] px-2 py-1 text-[13px]',
        line.late ? 'bg-k-fdecea text-k-b3261e' : line.done ? 'bg-k-e3f6ea text-k-0a6b35' : 'bg-k-efeaff text-k-4a36c4'
      )}
    >
      <span aria-hidden='true' className={cn('flex size-5 items-center justify-center rounded-md', line.late ? 'bg-k-d93025 text-k-ffffff' : 'bg-k-ffffff/60')}>
        <Icon className='size-3' />
      </span>
      <strong>{line.head}</strong>
      <span className='text-k-202020'>{line.text}</span>
      <Link to='/tasks' search={{ task: task.id } as never} className='font-bold text-k-5b45e0 hover:underline'>
        {line.done ? 'See it' : 'Edit'}
      </Link>
      {onNotAPromise && !line.done && (
        <>
          <span aria-hidden='true'>·</span>
          <button type='button' disabled={busy} onClick={onNotAPromise} className='font-bold text-k-555555 hover:underline disabled:opacity-50'>
            Not a promise
          </button>
        </>
      )}
    </span>
  )
}

/** Over the chat while a promise of yours is late: done, or 15 minutes more. */
export function LateBanner({
  task,
  nowMs,
  tz,
  busy,
  onDone,
  onLater,
}: {
  task: TaskRow
  nowMs: number
  tz: string
  busy: boolean
  onDone: () => void
  onLater: () => void
}) {
  const b = lateBanner(task, nowMs, tz)
  const doneLabel = task.kind === 'email' ? 'Sent · mark done' : task.kind === 'call' ? 'Called · mark done' : 'Mark done'
  return (
    <div role='status' className='mx-3 mt-2.5 flex flex-wrap items-center gap-3 rounded-[14px] px-3.5 py-2.5 ring-1 ring-k-f0cdc8'>
      <span aria-hidden='true' className='flex size-8 flex-none items-center justify-center rounded-full bg-k-fdecea text-k-b3261e'>
        <Clock className='size-4' />
      </span>
      <span className='flex min-w-[180px] flex-1 flex-col gap-0.5'>
        <strong className='text-sm text-k-202020'>
          {b.head} <span className='text-k-b3261e'>{b.late}</span>
        </strong>
        <span className='text-[13px] text-k-555555'>
          {task.title}. {b.text}
        </span>
      </span>
      <span className='flex flex-none gap-2'>
        <button type='button' disabled={busy} onClick={onDone} className='h-9 rounded-full bg-kb-202020 px-4 text-[13px] font-bold text-on-btn disabled:opacity-50'>
          {doneLabel}
        </button>
        <button type='button' disabled={busy} onClick={onLater} className='h-9 rounded-full border border-k-c9c9c9 px-3.5 text-[13px] font-bold text-k-202020 disabled:opacity-50'>
          +15 min
        </button>
      </span>
    </div>
  )
}
