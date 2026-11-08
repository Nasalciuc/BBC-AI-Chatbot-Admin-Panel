import { AlarmClock, Mail, MessageCircle, Phone, SquareCheck } from 'lucide-react'
import type { TaskKind, TaskRow as Row, TaskSource } from '@/lib/types'
import { cn } from '@/lib/utils'
import { PersonAvatar } from '@/components/person-avatar'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { clientLine, dueMain, dueSub, snoozeChoices, unownedNote, wroteAgain } from '../view'

const KIND_ICON: Record<TaskKind, { icon: React.ElementType; cls: string }> = {
  email: { icon: Mail, cls: 'bg-k-efeaff text-k-4a36c4' },
  call: { icon: Phone, cls: 'bg-k-e6f0fb text-k-1f5fb0' },
  todo: { icon: SquareCheck, cls: 'bg-k-f2f2f2 text-k-555555' },
}

const SOURCE_CHIP: Record<TaskSource, string> = {
  caught: 'bg-k-efeaff text-k-4a36c4',
  client_asked: 'bg-k-e6f0fb text-k-1f5fb0',
  added: 'bg-k-f2f2f2 text-k-555555',
  dashboard: 'bg-k-fff3e0 text-k-8a4b00',
}

export function KindIcon({ kind, size = 32 }: { kind: TaskKind; size?: 22 | 32 }) {
  const k = KIND_ICON[kind]
  const Icon = k.icon
  return (
    <span aria-hidden='true' className={cn('flex flex-none items-center justify-center', size === 32 ? 'size-8 rounded-[10px]' : 'size-[22px] rounded-md', k.cls)}>
      <Icon className={size === 32 ? 'size-4' : 'size-3'} />
    </span>
  )
}

/**
 * One task in the list: done circle, kind, title, the client and trip, where it
 * came from, its due time (red when late, with the client's time), and Snooze
 * and Open the chat. `showOwner`: the team view's column of who has it.
 */
export function TaskRow({
  row,
  nowMs,
  tz,
  selected,
  showOwner,
  busy,
  onOpen,
  onDone,
  onSnooze,
  onOpenChat,
}: {
  row: Row
  nowMs: number
  tz: string
  selected: boolean
  showOwner: boolean
  busy: boolean
  onOpen: () => void
  onDone: () => void
  onSnooze: (atMs: number, label: string) => void
  onOpenChat: (() => void) | null
}) {
  const late = row.state === 'late'
  const wrote = wroteAgain(row, tz)
  const unowned = !row.assignee_id ? unownedNote(row, tz) : null
  const client = row.source === 'dashboard' ? null : clientLine(row)
  const iconBtn = 'flex size-9 items-center justify-center rounded-full text-k-444444 hover:bg-k-f2f2f2 focus-visible:outline-2 focus-visible:outline-k-6ea8ff'
  const actions = (
    <>
      <Popover>
        <PopoverTrigger asChild>
          <button type='button' aria-label={`Snooze: ${row.title}`} title='Snooze' className={iconBtn}>
            <AlarmClock aria-hidden='true' className='size-[18px]' />
          </button>
        </PopoverTrigger>
        <PopoverContent align='end' className='flex w-auto flex-col gap-1 rounded-2xl p-1.5'>
          {snoozeChoices(nowMs, tz, row.due_at ? Date.parse(row.due_at) : undefined).map((c) => (
            <button key={c.label} type='button' onClick={() => onSnooze(c.at, c.label)} className='h-9 rounded-[10px] px-3 text-start text-sm text-k-202020 hover:bg-k-f2f2f2'>
              {c.label}
            </button>
          ))}
        </PopoverContent>
      </Popover>
      {onOpenChat ? (
        <button type='button' onClick={onOpenChat} aria-label={`Open the chat: ${row.title}`} title='Open the chat' className={iconBtn}>
          <MessageCircle aria-hidden='true' className='size-[18px]' />
        </button>
      ) : (
        <span aria-hidden='true' className='size-9' />
      )}
    </>
  )
  const sub = dueSub(row, nowMs, tz)
  return (
    <li
      className={cn(
        'relative flex items-start gap-3 border-t border-k-ececec px-4 py-3.5 first:border-t-0 sm:px-[18px]',
        selected && 'bg-k-f7f7f7 before:absolute before:inset-y-0 before:start-0 before:w-[3px] before:bg-k-202020'
      )}
    >
      <button
        type='button'
        onClick={onDone}
        disabled={busy}
        aria-label={`Mark done: ${row.title}`}
        title='Mark done'
        className='mt-0.5 flex size-[22px] flex-none items-center justify-center rounded-full border-2 border-k-c9c9c9 hover:border-k-0a6b35 focus-visible:outline-2 focus-visible:outline-k-6ea8ff disabled:opacity-50'
      />
      <KindIcon kind={row.kind} />
      <div className='flex min-w-0 flex-1 flex-col gap-1'>
        <button type='button' onClick={onOpen} aria-expanded={selected} className='flex min-w-0 flex-col gap-1 text-start focus-visible:outline-2 focus-visible:outline-k-6ea8ff'>
          <span className='text-[15px] leading-[1.35] font-bold text-k-202020'>{row.title}</span>
          {client && <span className='text-[13px] text-k-555555'>{client}</span>}
          {row.source === 'dashboard' && row.quote && <span className='text-[13px] text-k-555555'>From Dashboard · {row.quote}</span>}
          <span className='flex flex-wrap items-center gap-x-2 gap-y-1'>
            <span className={cn('rounded-md px-1.5 py-px text-xs font-bold', SOURCE_CHIP[row.source])}>{row.source_label}</span>
            {row.source !== 'dashboard' && row.quote && <span className='text-[13px] text-k-6b6b6b italic'>“{row.quote}”</span>}
            {row.source === 'added' && row.created_by_name && <span className='text-[13px] text-k-6b6b6b italic'>Added by {row.created_by_name}</span>}
          </span>
          {(wrote || unowned) && (
            <span className='flex flex-wrap gap-1.5'>
              {wrote && <span className='rounded-md bg-k-efeaff px-1.5 py-px text-xs font-bold text-k-4a36c4'>{wrote}</span>}
              {unowned && <span className='rounded-md bg-k-fff3e0 px-1.5 py-px text-xs font-bold text-k-8a4b00'>{unowned}</span>}
            </span>
          )}
          {showOwner && (
            <span className='text-[13px] text-k-555555 lg:hidden'>
              {row.assignee_id ? row.assignee_name : <strong className='text-k-8a4b00'>Unowned</strong>}
            </span>
          )}
        </button>
        {/* On a phone the due time and the actions sit under the text, not in squeezed columns. */}
        <span className='flex items-center gap-2 sm:hidden'>
          <span className={cn('min-w-0 flex-1 text-[13px] leading-[1.35]', late ? 'text-k-b3261e' : 'text-k-555555')}>
            <strong className={late ? 'text-k-b3261e' : 'text-k-202020'}>{dueMain(row, nowMs, tz)}</strong>
            {sub && ` · ${sub}`}
          </span>
          <span className='-me-2 flex flex-none items-center gap-0.5'>{actions}</span>
        </span>
      </div>
      {showOwner && (
        <span className='hidden w-[150px] flex-none items-center gap-2 pt-0.5 text-sm text-k-202020 lg:flex'>
          {row.assignee_id ? (
            <>
              <PersonAvatar name={row.assignee_name} size={30} />
              <span className='truncate'>{row.assignee_name}</span>
            </>
          ) : (
            <>
              <span aria-hidden='true' className='flex size-[30px] flex-none items-center justify-center rounded-full bg-k-fff3e0 text-xs font-bold text-k-8a4b00'>?</span>
              <span className='font-bold text-k-8a4b00'>Unowned</span>
            </>
          )}
        </span>
      )}
      <span className='hidden w-[170px] flex-none flex-col items-end gap-0.5 pt-0.5 text-end sm:flex'>
        <span className={cn('text-[15px] font-bold', late ? 'text-k-b3261e' : 'text-k-202020')}>{dueMain(row, nowMs, tz)}</span>
        <span className={cn('text-xs leading-[1.35]', late ? 'text-k-b3261e' : 'text-k-6b6b6b')}>{sub}</span>
      </span>
      <span className='hidden flex-none items-center gap-0.5 sm:flex'>{actions}</span>
    </li>
  )
}
