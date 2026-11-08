import { Link } from '@tanstack/react-router'
import { AlertCircle, ArrowUpRight } from 'lucide-react'
import type { ChatRef } from '@/lib/types'
import { cn } from '@/lib/utils'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import type { AttentionView, Tone } from './view'

export const linkCls =
  'text-sm font-bold text-k-5b45e0 hover:underline focus-visible:outline-2 focus-visible:outline-k-6ea8ff text-left'

export const labelCls = 'text-[11px] font-bold tracking-[0.05em] text-k-555555 uppercase'

const toneCls: Record<Tone, string> = {
  good: 'text-k-0a6b35',
  bad: 'text-k-b3261e',
  same: 'text-k-6b6b6b',
}

export function Delta({ d }: { d: { text: string; tone: Tone } | null }) {
  if (!d) return null
  return <span className={cn('text-[13px] font-bold', toneCls[d.tone])}>{d.text}</span>
}

/** One number with its story. `tint` paints the card (Ready to quote is cream). */
export function StatCard({
  label,
  value,
  delta,
  children,
  action,
  tint = 'bg-k-f7f7f7',
}: {
  label: string
  value: string
  delta?: { text: string; tone: Tone } | null
  children?: React.ReactNode
  action?: React.ReactNode
  tint?: string
}) {
  return (
    <section aria-label={label} className={cn('flex min-w-0 flex-col gap-2 rounded-[20px] p-[18px]', tint)}>
      <span className={labelCls}>{label}</span>
      <div className='flex flex-wrap items-baseline gap-x-2.5 gap-y-1'>
        <span className='text-[32px] leading-none font-bold text-k-202020'>{value}</span>
        <Delta d={delta ?? null} />
      </div>
      {children && <div className='flex-1 text-sm leading-[1.45] text-k-444444'>{children}</div>}
      {action && <div className='mt-1'>{action}</div>}
    </section>
  )
}

/** A white card with a ring: the page's sections. */
export function Panel({
  title,
  hint,
  id,
  children,
  className,
  right,
}: {
  title: string
  hint?: React.ReactNode
  id?: string
  children: React.ReactNode
  className?: string
  right?: React.ReactNode
}) {
  return (
    <section id={id} aria-label={title} className={cn('flex min-w-0 flex-col gap-3 rounded-[20px] p-5 ring-1 ring-k-ececec', className)}>
      <div className='flex flex-wrap items-baseline gap-x-3 gap-y-1'>
        <h2 className='m-0 text-lg font-bold text-k-202020'>{title}</h2>
        {hint && <span className='text-sm text-k-6b6b6b'>{hint}</span>}
        {right && <div className='ms-auto'>{right}</div>}
      </div>
      {children}
    </section>
  )
}

/** The purple sentence at the top of a tab. */
export function Headline({ text }: { text: string }) {
  const dot = text.indexOf('.')
  const first = dot > 0 ? text.slice(0, dot + 1) : text
  const rest = dot > 0 ? text.slice(dot + 1) : ''
  return (
    <div role='status' className='flex items-start gap-3 rounded-[18px] bg-k-f3f0ff px-4 py-3.5 text-[15px] leading-[1.5] text-k-2c2266'>
      <span aria-hidden='true' className='mt-0.5 flex size-6 flex-none items-center justify-center rounded-full bg-k-5b45e0 text-on-btn'>
        <ArrowUpRight className='size-3.5' />
      </span>
      <p className='m-0'>
        <strong>{first}</strong>
        {rest}
      </p>
    </div>
  )
}

/** "Open": the chats a number points at, each one a link to Conversations. */
export function OpenChats({
  chats,
  label = 'Open',
  count,
  teamOnly = false,
}: {
  chats: ChatRef[]
  label?: string
  count?: number
  /** The list keeps only the viewer's own teams' chats (supervisor, PM). */
  teamOnly?: boolean
}) {
  if (!chats.length) return null
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button type='button' className={linkCls}>
          {label}
        </button>
      </PopoverTrigger>
      <PopoverContent align='end' className='w-72 rounded-2xl p-2'>
        <p className='px-2 pt-1 pb-2 text-xs text-k-6b6b6b'>
          {teamOnly
            ? `${chats.length} of ${count ?? chats.length} ${(count ?? chats.length) === 1 ? 'is' : 'are'} your team’s; only those open for you.`
            : count && count > chats.length
              ? `The ${chats.length} newest of ${count}.`
              : `${chats.length} ${chats.length === 1 ? 'chat' : 'chats'}.`}{' '}
          Opens in Conversations.
        </p>
        <ul className='m-0 flex max-h-72 list-none flex-col overflow-y-auto p-0'>
          {chats.map((c) => (
            <li key={c.id}>
              <Link
                to='/chats'
                search={{ highlight: c.id } as never}
                className='flex items-center justify-between gap-2 rounded-xl px-2 py-2 text-sm text-k-202020 hover:bg-row-hover'
              >
                <span className='truncate'>{c.first_name || 'A client'}</span>
                {c.chat_number ? <span className='text-xs text-k-6b6b6b'>#{c.chat_number}</span> : null}
              </Link>
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  )
}

export function AttentionList({
  items,
  onAction,
  canOpen = () => true,
  note,
  empty = 'Nothing crossed a line in this period.',
}: {
  items: AttentionView[]
  onAction: (to: AttentionView['action']['to']) => void
  /** Whether an action can open here; one that can't (another tab, on a phone) isn't drawn. */
  canOpen?: (to: AttentionView['action']['to']) => boolean
  note: string
  empty?: string
}) {
  return (
    <div className='flex flex-col'>
      {items.length === 0 ? (
        <p className='m-0 py-2 text-sm text-k-555555'>{empty}</p>
      ) : (
        <ul className='m-0 flex list-none flex-col p-0'>
          {items.map((a) => (
            <li key={a.key} className='flex items-start gap-3 border-t border-k-ececec py-3.5 first:border-t-0 first:pt-1'>
              <span
                aria-hidden='true'
                className={cn(
                  'mt-0.5 flex size-7 flex-none items-center justify-center rounded-full',
                  a.level === 'urgent' ? 'bg-k-fdecea text-k-b3261e' : 'bg-k-fff3e0 text-k-8a4b00'
                )}
              >
                <AlertCircle className='size-4' />
              </span>
              <div className='flex min-w-0 flex-1 flex-col gap-1'>
                <p className='m-0 text-[15px] leading-[1.4] text-k-202020'>
                  <span className={cn('me-2 text-[11px] font-bold tracking-[0.05em] uppercase', a.level === 'urgent' ? 'text-k-b3261e' : 'text-k-8a4b00')}>
                    {a.tag}
                  </span>
                  {a.title}
                </p>
                <p className='m-0 text-[13px] text-k-6b6b6b'>{a.sub}</p>
                {/* On a phone the action sits under the text, not in a squeezed column. */}
                {canOpen(a.action.to) && (
                  <button type='button' onClick={() => onAction(a.action.to)} className={cn(linkCls, 'mt-0.5 self-start sm:hidden')}>
                    {a.action.label}
                  </button>
                )}
              </div>
              {canOpen(a.action.to) && (
                <button type='button' onClick={() => onAction(a.action.to)} className={cn(linkCls, 'hidden flex-none sm:block')}>
                  {a.action.label}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      <p className='m-0 mt-2 text-xs text-k-6b6b6b'>{note}</p>
    </div>
  )
}

/** Small text tabs (Source / Site / Device …). */
export function Pills<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T
  options: { key: T; label: string }[]
  onChange: (v: T) => void
  label: string
}) {
  return (
    <div role='tablist' aria-label={label} className='flex flex-wrap gap-1 rounded-2xl bg-k-f2f2f2 p-1'>
      {options.map((o) => (
        <button
          key={o.key}
          type='button'
          role='tab'
          aria-selected={value === o.key}
          onClick={() => onChange(o.key)}
          className={cn(
            'h-8 rounded-xl px-3 text-[13px] focus-visible:outline-2 focus-visible:outline-k-6ea8ff',
            value === o.key ? 'bg-k-ffffff font-bold text-k-202020 shadow-sm' : 'text-k-444444'
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Th({ children, className }: { children?: React.ReactNode; className?: string }) {
  return <th className={cn('px-3 py-2.5 text-left text-[11px] font-bold tracking-[0.05em] text-k-555555 uppercase', className)}>{children}</th>
}

export function Td({ children, className }: { children?: React.ReactNode; className?: string }) {
  return <td className={cn('px-3 py-3 align-top text-sm text-k-202020', className)}>{children}</td>
}
