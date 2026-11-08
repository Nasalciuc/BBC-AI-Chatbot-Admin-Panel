import { useDeferredValue, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getConversations } from '@/lib/api'
import type { NewTaskBody, TaskKind, TaskPerson } from '@/lib/types'
import { cn } from '@/lib/utils'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { KIND_LABEL, newTaskChoices, pickedInstant, wallClock } from '../view'

const field = 'flex flex-col gap-1.5'
const labelCls = 'text-[13px] font-bold text-k-202020'
const seg = (on: boolean) =>
  cn(
    'h-9 rounded-full px-3.5 text-[13px] font-bold focus-visible:outline-2 focus-visible:outline-k-6ea8ff',
    on ? 'bg-kb-202020 text-on-btn' : 'bg-k-f2f2f2 text-k-202020 hover:bg-k-ececec'
  )

/**
 * New task: type, what, for whom, when, and an optional chat to link. A task
 * stays in the panel: the CRM takes leads, not tasks. `link`: opened with a
 * chat or a Dashboard line already chosen.
 */
export function NewTaskDialog({
  open,
  onOpenChange,
  nowMs,
  tz,
  meId,
  people,
  canAssign,
  busy,
  onAdd,
  link,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  nowMs: number
  tz: string
  meId: string
  people: TaskPerson[]
  canAssign: boolean
  busy: boolean
  onAdd: (body: NewTaskBody) => void
  link?: { conversationId?: string; label: string; source?: 'added' | 'dashboard'; note?: string; title?: string; assigneeId?: string } | null
}) {
  const [kind, setKind] = useState<TaskKind>('todo')
  const [title, setTitle] = useState(link?.title ?? '')
  const [forId, setForId] = useState<string>(link?.assigneeId ?? meId)
  // The choices are fixed when the dialog opens: "Today 17:00" can't slip
  // away from under a selection while it's open.
  const [openedAt] = useState(nowMs)
  const choices = useMemo(() => newTaskChoices(openedAt, tz), [openedAt, tz])
  const [due, setDue] = useState<string>('0')
  const today = wallClock(openedAt, tz)
  const [date, setDate] = useState(`${today.y}-${String(today.mo).padStart(2, '0')}-${String(today.d).padStart(2, '0')}`)
  const [time, setTime] = useState('')
  const [q, setQ] = useState('')
  const [chat, setChat] = useState<{ id: string; label: string } | null>(link?.conversationId ? { id: link.conversationId, label: link.label } : null)
  const search = useDeferredValue(q.trim())
  const found = useQuery({
    queryKey: ['task-link-search', search],
    queryFn: () => getConversations({ search, limit: '6' }),
    enabled: open && !chat && !link?.note && search.length >= 2,
    staleTime: 30_000,
  })

  const dueIso = (): { due_at: string | null; due_kind: 'picked' | 'day' } => {
    if (due === 'pick') {
      // A day without a time is due by the end of that day ("no time set").
      const iso = pickedInstant(date, time || '23:00', tz)
      return { due_at: iso, due_kind: time ? 'picked' : 'day' }
    }
    const c = choices[Number(due)]
    return { due_at: c ? new Date(c.at).toISOString() : null, due_kind: 'picked' }
  }

  const submit = () => {
    const t = title.trim()
    if (!t) return
    const { due_at, due_kind } = dueIso()
    onAdd({
      kind,
      title: t,
      due_at,
      due_kind,
      ...(forId === '' ? { unowned: true } : { assignee_id: forId }),
      conversation_id: chat?.id ?? null,
      source: link?.source ?? 'added',
      note: link?.note ?? null,
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className='flex max-h-[92svh] flex-col gap-4 overflow-y-auto rounded-[20px] bg-k-ffffff p-6 sm:max-w-[480px]'>
        <div className='flex flex-col gap-1'>
          <DialogTitle className='m-0 text-[22px] font-bold text-k-202020'>New task</DialogTitle>
          <DialogDescription className='m-0 text-[13px] text-k-555555'>Tasks stay in the panel: the CRM takes leads, not tasks.</DialogDescription>
        </div>

        <div className={field}>
          <span className={labelCls} id='new-task-type'>Type</span>
          <span role='radiogroup' aria-labelledby='new-task-type' className='flex flex-wrap gap-1.5'>
            {(['call', 'email', 'todo'] as const).map((k) => (
              <button key={k} type='button' role='radio' aria-checked={kind === k} onClick={() => setKind(k)} className={seg(kind === k)}>
                {KIND_LABEL[k]}
              </button>
            ))}
          </span>
        </div>

        <label className={field}>
          <span className={labelCls}>What</span>
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            maxLength={300}
            placeholder='Send the group booking rules to the Kumar family'
            className='h-11 rounded-[12px] border border-k-c9c9c9 bg-k-ffffff px-3 text-[15px] text-k-202020 placeholder:text-k-8a8a8a focus-visible:outline-2 focus-visible:outline-k-6ea8ff'
          />
        </label>

        <label className={field}>
          <span className={labelCls}>For</span>
          {canAssign ? (
            <select value={forId} onChange={(e) => setForId(e.target.value)} className='h-11 rounded-[12px] border border-k-c9c9c9 bg-k-ffffff px-3 text-[15px] text-k-202020'>
              <option value={meId}>Me</option>
              {people.filter((p) => p.id !== meId).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
              <option value=''>Unowned (someone gives it out)</option>
            </select>
          ) : (
            <span className='text-[15px] text-k-202020'>Me</span>
          )}
        </label>

        <div className={field}>
          <span className={labelCls} id='new-task-due'>Due</span>
          <span role='radiogroup' aria-labelledby='new-task-due' className='flex flex-wrap gap-1.5'>
            {choices.map((c, i) => (
              <button key={c.label} type='button' role='radio' aria-checked={due === String(i)} onClick={() => setDue(String(i))} className={seg(due === String(i))}>
                {c.label}
              </button>
            ))}
            <button type='button' role='radio' aria-checked={due === 'pick'} onClick={() => setDue('pick')} className={seg(due === 'pick')}>
              Pick a date…
            </button>
          </span>
          {due === 'pick' && (
            <span className='flex flex-wrap items-center gap-2'>
              <input aria-label='Day' type='date' value={date} onChange={(e) => setDate(e.target.value)} className='h-10 rounded-[10px] border border-k-c9c9c9 bg-k-ffffff px-2.5 text-sm text-k-202020' />
              <input aria-label='Time (optional)' type='time' value={time} onChange={(e) => setTime(e.target.value)} className='h-10 rounded-[10px] border border-k-c9c9c9 bg-k-ffffff px-2.5 text-sm text-k-202020' />
              <span className='text-xs text-k-6b6b6b'>No time: due by the end of that day.</span>
            </span>
          )}
        </div>

        {link?.note ? (
          <div className={field}>
            <span className={labelCls}>From the Dashboard</span>
            <span className='rounded-[12px] bg-k-fff3e0 px-3 py-2 text-[13px] text-k-8a4b00'>{link.note}</span>
          </div>
        ) : (
          <div className={field}>
            <span className={labelCls}>Linked to a chat (optional)</span>
            {chat ? (
              <span className='flex items-center gap-2 rounded-[12px] bg-k-f7f7f7 px-3 py-2 text-sm text-k-202020'>
                <span className='min-w-0 flex-1 truncate'>{chat.label}</span>
                {!link?.conversationId && (
                  <button type='button' onClick={() => setChat(null)} className='text-[13px] font-bold text-k-5b45e0'>
                    Change
                  </button>
                )}
              </span>
            ) : (
              <>
                <input
                  type='search'
                  value={q}
                  onChange={(e) => setQ(e.target.value)}
                  placeholder='Search chats by name, e-mail or chat number'
                  className='h-11 rounded-[12px] border border-k-c9c9c9 bg-k-ffffff px-3 text-[15px] text-k-202020 placeholder:text-k-8a8a8a'
                />
                {search.length >= 2 && (
                  <ul className='m-0 flex list-none flex-col p-0'>
                    {found.isError ? (
                      <li className='px-1 py-1.5 text-[13px] text-k-b3261e'>Couldn’t search the chats.</li>
                    ) : found.isLoading ? (
                      <li className='px-1 py-1.5 text-[13px] text-k-6b6b6b'>Searching…</li>
                    ) : (found.data?.data ?? []).length === 0 ? (
                      <li className='px-1 py-1.5 text-[13px] text-k-6b6b6b'>No chat matches.</li>
                    ) : (
                      (found.data?.data ?? []).map((c) => {
                        const text = [c.visitor_name || 'Visitor', c.chat_number != null ? `#${c.chat_number}` : null].filter(Boolean).join(' · ')
                        return (
                          <li key={c.id}>
                            <button type='button' onClick={() => setChat({ id: c.id, label: text })} className='w-full rounded-[10px] px-2 py-2 text-start text-sm text-k-202020 hover:bg-k-f2f2f2'>
                              {text}
                            </button>
                          </li>
                        )
                      })
                    )}
                  </ul>
                )}
              </>
            )}
          </div>
        )}

        <div className='flex justify-end gap-2 pt-1'>
          <button type='button' onClick={() => onOpenChange(false)} className='h-11 rounded-full border border-k-c9c9c9 px-5 text-sm font-bold text-k-202020'>
            Cancel
          </button>
          <button type='button' disabled={!title.trim() || busy} onClick={submit} className='h-11 rounded-full bg-kb-202020 px-5 text-sm font-bold text-on-btn disabled:opacity-50'>
            {busy ? 'Adding…' : 'Add task'}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
