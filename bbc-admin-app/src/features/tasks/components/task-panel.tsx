import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { X } from 'lucide-react'
import { getTaskDetail } from '@/lib/api'
import type { TaskPerson, TaskRow } from '@/lib/types'
import { cn } from '@/lib/utils'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { clientLine, clockIn, dueMain, dueSub, pickedInstant, snoozeChoices, unownedNote, wallClock } from '../view'

const label = 'w-[72px] flex-none text-[13px] text-k-6b6b6b'
const chip = 'h-8 rounded-full bg-k-f2f2f2 px-3 text-[13px] font-bold text-k-202020 hover:bg-k-ececec focus-visible:outline-2 focus-visible:outline-k-6ea8ff disabled:opacity-50'

export type PanelActions = {
  busy: boolean
  onDone: (done: boolean) => void
  onSnooze: (atMs: number, label: string) => void
  onPickDue: (iso: string) => void
  onAssign: (assigneeId: string | null) => void
  onNotAPromise: () => void
  onDelete: () => void
  onOpenChat: (conversationId: string) => void
}

const DONE_LABEL = { email: 'Sent · mark done', call: 'Called · mark done', todo: 'Mark done' } as const

/**
 * One task: what, when (with quick new times), for whom, the client, the CRM
 * line, the chat it came from and what happened to it. The list row draws
 * the top at once; the chat excerpt and history come with the detail read.
 */
export function TaskPanel({
  row,
  nowMs,
  tz,
  people,
  canAssign,
  onClose,
  actions,
}: {
  row: TaskRow
  nowMs: number
  tz: string
  people: TaskPerson[]
  canAssign: boolean
  onClose: () => void
  actions: PanelActions
}) {
  const detail = useQuery({ queryKey: ['task', row.id], queryFn: () => getTaskDetail(row.id), select: (r) => r.data, retry: 1 })
  const d = detail.data
  const [picking, setPicking] = useState(false)
  const [date, setDate] = useState('')
  const [time, setTime] = useState('10:00')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const late = row.state === 'late'
  const done = !!row.done_at
  const canEdit = d ? d.can.edit : true
  const unowned = !row.assignee_id ? unownedNote(row, tz) : null
  const today = wallClock(nowMs, tz)

  return (
    <div className='flex flex-col gap-4'>
      <div className='flex items-start gap-2'>
        <span className='flex flex-1 flex-wrap gap-1.5'>
          <span className='rounded-full bg-k-efeaff px-2 py-0.5 text-xs font-bold text-k-4a36c4'>{row.kind_label}</span>
          {late && !done && <span className='rounded-full bg-k-fdecea px-2 py-0.5 text-xs font-bold text-k-b3261e'>Late</span>}
          {done && <span className='rounded-full bg-k-e3f6ea px-2 py-0.5 text-xs font-bold text-k-0a6b35'>Done</span>}
        </span>
        <button type='button' onClick={onClose} aria-label='Close the task' className='-me-1 -mt-1 flex size-9 items-center justify-center rounded-full text-k-444444 hover:bg-k-f2f2f2'>
          <X aria-hidden='true' className='size-[18px]' />
        </button>
      </div>
      <h2 className='m-0 text-[22px] leading-[1.25] font-bold text-k-202020'>{row.title}</h2>

      <dl className='m-0 flex flex-col gap-3'>
        <div className='flex gap-3'>
          <dt className={label}>Due</dt>
          <dd className='m-0 flex min-w-0 flex-1 flex-col gap-2'>
            <span className='text-sm text-k-555555'>
              <strong className={cn('text-[15px]', late && !done ? 'text-k-b3261e' : 'text-k-202020')}>{dueMain(row, nowMs, tz)}</strong>{' '}
              {done ? '' : dueSub(row, nowMs, tz)}
            </span>
            {canEdit && !done && (
              <span className='flex flex-wrap gap-1.5'>
                {snoozeChoices(nowMs, tz, row.due_at ? Date.parse(row.due_at) : undefined).map((c) => (
                  <button key={c.label} type='button' disabled={actions.busy} onClick={() => actions.onSnooze(c.at, c.label)} className={chip}>
                    {c.label}
                  </button>
                ))}
                <button type='button' onClick={() => setPicking((p) => !p)} aria-expanded={picking} className={chip}>
                  Pick…
                </button>
              </span>
            )}
            {picking && (
              <span className='flex flex-wrap items-center gap-2'>
                <label className='sr-only' htmlFor='task-due-date'>Day</label>
                <input
                  id='task-due-date'
                  type='date'
                  value={date}
                  min={`${today.y}-${String(today.mo).padStart(2, '0')}-${String(today.d).padStart(2, '0')}`}
                  onChange={(e) => setDate(e.target.value)}
                  className='h-9 rounded-[10px] border border-k-c9c9c9 bg-k-ffffff px-2.5 text-sm text-k-202020'
                />
                <label className='sr-only' htmlFor='task-due-time'>Time</label>
                <input id='task-due-time' type='time' value={time} onChange={(e) => setTime(e.target.value)} className='h-9 rounded-[10px] border border-k-c9c9c9 bg-k-ffffff px-2.5 text-sm text-k-202020' />
                <button
                  type='button'
                  disabled={!date || actions.busy}
                  onClick={() => {
                    const iso = pickedInstant(date, time, tz)
                    if (iso) {
                      actions.onPickDue(iso)
                      setPicking(false)
                    }
                  }}
                  className='h-9 rounded-full bg-kb-202020 px-3.5 text-[13px] font-bold text-on-btn disabled:opacity-50'
                >
                  Set
                </button>
              </span>
            )}
          </dd>
        </div>
        <div className='flex gap-3'>
          <dt className={label}>For</dt>
          <dd className='m-0 flex min-w-0 flex-1 flex-col gap-1'>
            {canAssign && !done ? (
              <select
                aria-label='Who has this task'
                value={row.assignee_id ?? ''}
                disabled={actions.busy}
                onChange={(e) => actions.onAssign(e.target.value || null)}
                className='h-9 max-w-[260px] rounded-[10px] border border-k-c9c9c9 bg-k-ffffff px-2.5 text-sm font-bold text-k-202020'
              >
                <option value=''>Unowned</option>
                {people.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            ) : (
              <strong className='text-sm text-k-202020'>{row.assignee_name || 'Unowned'}</strong>
            )}
            {unowned && <span className='text-[13px] text-k-8a4b00'>{unowned}</span>}
          </dd>
        </div>
        {row.conversation_id && (
          <div className='flex gap-3'>
            <dt className={label}>Client</dt>
            <dd className='m-0 flex min-w-0 flex-1 flex-col gap-0.5 text-[13px] text-k-555555'>
              <strong className='text-sm text-k-202020'>{row.client || 'No name'}</strong>
              <span>{clientLine({ ...row, client: null })}</span>
              {d && (d.contact.email || d.contact.phone) && <span>{[d.contact.email, d.contact.phone].filter(Boolean).join(' · ')}</span>}
            </dd>
          </div>
        )}
        {d?.crm.has_lead && (
          <div className='flex gap-3'>
            <dt className={label}>CRM</dt>
            <dd className='m-0 flex-1 text-[13px] text-k-555555'>
              {d.crm.in_crm ? 'In the CRM.' : 'Not in the CRM yet.'} The task stays here: the CRM takes leads, not tasks.
            </dd>
          </div>
        )}
      </dl>

      {row.conversation_id && (
        <section aria-label='Where it came from, in the chat' className='flex flex-col gap-2.5 rounded-2xl bg-k-f7f7f7 p-3.5'>
          <span className='flex items-center justify-between gap-2'>
            <strong className='text-sm text-k-202020'>Where it came from, in the chat</strong>
            <button type='button' onClick={() => actions.onOpenChat(row.conversation_id!)} className='text-sm font-bold text-k-5b45e0 hover:underline'>
              Open the chat
            </button>
          </span>
          {detail.isError ? (
            <span className='text-[13px] text-k-b3261e'>Couldn’t load the chat. <button type='button' onClick={() => detail.refetch()} className='font-bold underline'>Try again</button></span>
          ) : !d ? (
            <span className='h-16 animate-pulse rounded-xl bg-k-ececec' aria-busy='true' />
          ) : d.excerpt.length === 0 ? (
            <span className='text-[13px] text-k-6b6b6b'>No messages around it.</span>
          ) : (
            <ol className='m-0 flex list-none flex-col gap-2.5 p-0'>
              {d.excerpt.map((m, i) => (
                <li key={m.id ?? i} className='flex gap-2.5'>
                  <span aria-hidden='true' className={cn('flex size-6 flex-none items-center justify-center rounded-full text-[11px] font-bold', m.role === 'agent' ? 'bg-k-202020 text-k-ffffff' : m.role === 'user' ? 'bg-k-e6f0fb text-k-1f5fb0' : 'bg-k-efeaff text-k-4a36c4')}>
                    {(m.who || '?').slice(0, 1).toUpperCase()}
                  </span>
                  <span className='flex min-w-0 flex-1 flex-col gap-0.5'>
                    <span className={cn('text-xs', m.source ? 'font-bold text-k-7a5300' : m.after ? 'font-bold text-k-4a36c4' : 'text-k-6b6b6b')}>
                      {m.who}
                      {m.at ? ` · ${clockIn(Date.parse(m.at), tz)}` : ''}
                      {m.source ? ` · ${row.source === 'client_asked' ? 'the ask' : 'the promise'}` : m.after ? ' · new' : ''}
                    </span>
                    <span
                      className={cn(
                        'w-fit max-w-full text-sm leading-[1.45] [overflow-wrap:anywhere] text-k-202020',
                        m.source && 'rounded-lg bg-k-fff1dc px-2.5 py-1.5',
                        m.after && 'rounded-lg border-s-[3px] border-k-4a36c4 bg-k-efeaff px-2.5 py-1.5'
                      )}
                    >
                      {m.text}
                    </span>
                  </span>
                </li>
              ))}
            </ol>
          )}
        </section>
      )}

      {d && d.history.length > 0 && (
        <section aria-label='History' className='flex flex-col gap-1.5'>
          <strong className='text-sm text-k-202020'>History</strong>
          <ol className='m-0 flex list-none flex-col gap-1 p-0'>
            {d.history.map((h, i) => (
              <li key={`${h.at}-${i}`} className='flex gap-3 text-[13px]'>
                <span className='w-[44px] flex-none text-k-6b6b6b'>{h.at ? clockIn(Date.parse(h.at), tz) : ''}</span>
                <span className='text-k-202020'>{h.text}</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {canEdit && (
        <div className='flex flex-wrap items-center gap-2'>
          <button
            type='button'
            disabled={actions.busy}
            onClick={() => actions.onDone(!done)}
            className={cn('h-11 rounded-full px-5 text-sm font-bold disabled:opacity-50', done ? 'border border-k-c9c9c9 text-k-202020' : 'bg-kb-202020 text-on-btn')}
          >
            {done ? 'Open again' : DONE_LABEL[row.kind]}
          </button>
          {!done && (d ? d.can.not_a_promise : row.source === 'caught') && (
            <button type='button' disabled={actions.busy} onClick={actions.onNotAPromise} className='h-11 rounded-full border border-k-c9c9c9 px-5 text-sm font-bold text-k-202020 disabled:opacity-50'>
              Not a promise
            </button>
          )}
          {d?.can.delete && (
            <button type='button' onClick={() => setConfirmDelete(true)} className='ms-auto text-[13px] font-bold text-k-b3261e hover:underline'>
              Delete
            </button>
          )}
        </div>
      )}
      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title='Delete this task?'
        desc='It goes from everyone’s list. Use Done when it was done.'
        confirmText='Delete'
        destructive
        handleConfirm={() => {
          setConfirmDelete(false)
          actions.onDelete()
        }}
      />
    </div>
  )
}
