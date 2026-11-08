import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { ChevronDown, ChevronUp } from 'lucide-react'
import { toast } from 'sonner'
import { type ApiError, reassignConversation } from '@/lib/api'
import type { FloorChat, FloorPerson, TeamNow } from '@/lib/types'
import { cn } from '@/lib/utils'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { PersonAvatar } from '@/components/person-avatar'
import {
  PRESENCE,
  attentionLine,
  howChatsReachPeople,
  isProblem,
  plural,
  secondsText,
  statCards,
  timer,
  timerTone,
  withPersonLine,
} from '../view'

const toneText = { bad: 'text-k-b3261e', warn: 'text-k-8a4b00', muted: 'text-k-555555', ok: 'text-k-0a6b35', ink: 'text-k-202020' } as const
const timerChip = { bad: 'bg-k-fdecea text-k-b3261e', warn: 'bg-k-fff1dc text-k-8a4b00', ok: 'bg-k-e3f6ea text-k-0a6b35', muted: 'bg-k-f2f2f2 text-k-555555' } as const
const btn = 'h-9 rounded-full px-3.5 text-[13px] font-bold focus-visible:outline-2 focus-visible:outline-k-6ea8ff'

/** "Give to…": the people of that tunnel who can take it, Ready first. */
function GiveTo({ chat, people, primary }: { chat: FloorChat; people: FloorPerson[]; primary?: boolean }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const give = useMutation({
    mutationFn: (p: FloorPerson) => reassignConversation(chat.id, p.id),
    onSuccess: (_r, p) => {
      toast.success(`Given to ${p.name ?? 'them'}.`)
      setOpen(false)
      qc.invalidateQueries({ queryKey: ['team-now'] })
    },
    onError: (e) => toast.error((e as ApiError)?.message ?? 'Couldn’t give the chat. Try again.'),
  })
  const who = people.filter((p) => p.tunnel === chat.tunnel && p.presence !== 'offline')
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button type='button' className={cn(btn, primary ? 'bg-kb-202020 text-on-btn' : 'border border-k-d9d9d9 text-k-202020')}>
          Give to…
        </button>
      </PopoverTrigger>
      <PopoverContent align='end' className='w-72 rounded-2xl p-2'>
        <p className='px-2 pt-1 pb-2 text-xs text-k-6b6b6b'>
          {who.length ? 'The chat becomes theirs at once; the client isn’t told.' : `Nobody in ${chat.tunnel ?? 'this tunnel'} is online.`}
        </p>
        <ul className='m-0 flex max-h-72 list-none flex-col overflow-y-auto p-0'>
          {who.map((p) => (
            <li key={p.id}>
              <button
                type='button'
                disabled={give.isPending}
                onClick={() => give.mutate(p)}
                className='flex w-full items-center gap-2.5 rounded-xl px-2 py-2 text-start text-sm text-k-202020 hover:bg-row-hover disabled:opacity-50'
              >
                <span aria-hidden='true' className={cn('size-2 flex-none rounded-full', PRESENCE[p.presence].dot)} />
                <span className='min-w-0 flex-1 truncate'>{p.name}</span>
                <span className='text-xs text-k-6b6b6b'>{plural(p.chats_now, 'chat')}</span>
              </button>
            </li>
          ))}
        </ul>
      </PopoverContent>
    </Popover>
  )
}

function Card({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn('flex flex-col gap-1.5 rounded-[16px] bg-k-ffffff p-3.5 shadow-[0_0_0_1px_var(--k-ececec)]', className)}>{children}</div>
}

function ChatHead({ chat, since, nowMs, neutral = false }: { chat: FloorChat; since: string | null; nowMs: number; neutral?: boolean }) {
  // Waiting for the client is nobody's problem: the timer stays grey.
  const tone = neutral ? 'muted' : timerTone(since, nowMs)
  return (
    <>
      <div className='flex items-start justify-between gap-2'>
        <strong className='min-w-0 truncate text-[15px] text-k-202020'>{chat.client || (chat.chat_number ? `#${chat.chat_number}` : 'A client')}</strong>
        <span className={cn('flex-none rounded-full px-2 py-0.5 text-xs font-bold tabular-nums', timerChip[tone])}>{timer(since, nowMs)}</span>
      </div>
      <span className='text-xs text-k-6b6b6b'>
        {[chat.route, chat.tunnel === 'support' ? 'Support' : 'Sales', chat.messages ? plural(chat.messages, 'message') : null, chat.client.startsWith('•') && chat.chat_number ? `#${chat.chat_number}` : null]
          .filter(Boolean)
          .join(' · ')}
      </span>
    </>
  )
}

function OpenLink({ id }: { id: string }) {
  return (
    <Link to='/chats' search={{ highlight: id } as never} className={cn(btn, 'inline-flex items-center border border-k-d9d9d9 text-k-202020')}>
      Open
    </Link>
  )
}

/**
 * Team · Now: the floor as it is. Cards, Needs attention past fixed lines,
 * and every open chat by where it stands (or by person).
 */
export function Floor({
  d,
  nowMs,
  teamScoped,
  onPerson,
}: {
  d: TeamNow
  nowMs: number
  /** Supervisor or PM: queue chats have no team yet, so they don't open. */
  teamScoped: boolean
  onPerson: (id: string) => void
}) {
  const [by, setBy] = useState<'chat' | 'person'>('chat')
  const [problems, setProblems] = useState(false)
  const [how, setHow] = useState(true)
  const people = d.people ?? []
  const queue = d.queue ?? []
  const withPerson = d.with_person ?? []
  const canGive = !!d.can_give
  const attention = (d.attention ?? []).map((a) => attentionLine(a))
  const problemCount =
    queue.filter((q) => isProblem(q.since, nowMs, true)).length + withPerson.filter((c) => isProblem(c.since, nowMs, c.client_waiting)).length
  const shownQueue = problems ? queue.filter((q) => isProblem(q.since, nowMs, true)) : queue
  const shownWith = problems ? withPerson.filter((c) => isProblem(c.since, nowMs, c.client_waiting)) : withPerson

  const act = (a: ReturnType<typeof attentionLine>) => {
    if (a.action.kind === 'person' || a.action.kind === 'coverage') onPerson(a.action.id)
    else document.getElementById(`floor-${a.action.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  return (
    <div className='flex flex-col gap-[18px]'>
      <div className='grid grid-cols-1 gap-3.5 sm:grid-cols-2 xl:grid-cols-4'>
        {statCards(d).map((c) => (
          <section key={c.label} aria-label={c.label} className='flex flex-col gap-1.5 rounded-[20px] bg-k-f7f7f7 p-[18px]'>
            <span className='text-[11px] font-bold tracking-[0.05em] text-k-555555 uppercase'>{c.label}</span>
            <span className='flex items-baseline gap-2'>
              <span className={cn('text-[32px] leading-none font-bold', toneText[c.tone])}>{c.value}</span>
              {c.unit && <span className='text-sm text-k-555555'>{c.unit}</span>}
            </span>
            <span className='text-[13px] text-k-555555'>{c.sub}</span>
          </section>
        ))}
      </div>

      <div className='grid grid-cols-1 gap-[18px] xl:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]'>
        <section aria-label='Needs attention' className='flex flex-col gap-1 rounded-[20px] p-5 ring-1 ring-k-ececec'>
          <h2 className='m-0 flex items-center gap-2 text-lg font-bold text-k-202020'>
            Needs attention
            {attention.length > 0 && <span className='rounded-full bg-k-fdecea px-2 text-xs font-bold text-k-b3261e'>{attention.length}</span>}
          </h2>
          {attention.length === 0 ? (
            <p className='m-0 py-2 text-sm text-k-555555'>Nothing past a line right now.</p>
          ) : (
            <ul className='m-0 flex list-none flex-col p-0'>
              {attention.map((a) => (
                <li key={a.key} className='flex flex-wrap items-start gap-3 border-t border-k-ececec py-3 first:border-t-0'>
                  <span
                    aria-hidden='true'
                    className={cn('mt-1.5 size-2.5 flex-none rounded-full', a.tone === 'bad' ? 'bg-k-d93025' : a.tone === 'warn' ? 'bg-k-d97a00' : 'bg-k-9c9c9c')}
                  />
                  <p className='m-0 min-w-[200px] flex-1 text-[15px] leading-[1.45] text-k-202020'>
                    <strong>{a.who}.</strong> {a.text}
                  </p>
                  {a.action.kind === 'chat' ? (
                    <Link
                      to='/chats'
                      search={{ highlight: a.action.id } as never}
                      className={cn(btn, 'inline-flex items-center border border-k-c9c9c9 text-k-202020')}
                    >
                      {a.action.label}
                    </Link>
                  ) : (
                    <button type='button' onClick={() => act(a)} className={cn(btn, 'border border-k-c9c9c9 text-k-202020')}>
                      {a.action.label}
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
          <p className='m-0 mt-1 text-xs text-k-6b6b6b'>
            Shown only past a line: a client waiting 5 min for a reply, a Ready panel with no signal, the queue over 3 min, one person Ready in
            a tunnel, a chat back to the assistant today.
          </p>
        </section>
        {d.windows && (
          <section aria-label='How chats reach people' className='flex flex-col gap-2 self-start rounded-[20px] bg-k-f7f7f7 p-5'>
            <button type='button' onClick={() => setHow((v) => !v)} aria-expanded={how} className='flex items-center justify-between gap-2 text-left'>
              <h2 className='m-0 text-lg font-bold text-k-202020'>How chats reach people</h2>
              {how ? <ChevronUp className='size-4' /> : <ChevronDown className='size-4' />}
            </button>
            {how && (
              <ol className='m-0 flex flex-col gap-1.5 ps-5 text-sm leading-[1.45] text-k-444444'>
                {howChatsReachPeople(d.windows).map((t) => (
                  <li key={t}>{t}</li>
                ))}
              </ol>
            )}
          </section>
        )}
      </div>

      <div className='flex flex-wrap items-center gap-3'>
        <h2 className='m-0 text-xl font-bold text-k-202020'>On the floor</h2>
        <div role='tablist' aria-label='On the floor' className='flex gap-1 rounded-2xl bg-k-f2f2f2 p-1'>
          {(['chat', 'person'] as const).map((k) => (
            <button
              key={k}
              type='button'
              role='tab'
              aria-selected={by === k}
              onClick={() => setBy(k)}
              className={cn('h-8 rounded-xl px-3 text-[13px]', by === k ? 'bg-k-ffffff font-bold text-k-202020 shadow-sm' : 'text-k-444444')}
            >
              {k === 'chat' ? 'By chat' : 'By person'}
            </button>
          ))}
        </div>
        {by === 'chat' && (
          <button
            type='button'
            aria-pressed={problems}
            onClick={() => setProblems((v) => !v)}
            className={cn(
              'flex h-9 items-center gap-2 rounded-full border px-3.5 text-[13px] font-bold',
              problems ? 'border-kb-202020 bg-kb-202020 text-on-btn' : 'border-k-d9d9d9 text-k-202020'
            )}
          >
            <span aria-hidden='true' className='size-2 rounded-full bg-k-d93025' />
            Only problems · {problemCount}
          </button>
        )}
        <span className='ms-auto text-xs text-k-6b6b6b'>Live · timers count from the client’s last message</span>
      </div>

      {by === 'chat' ? (
        <div className='grid grid-cols-1 gap-3.5 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)_minmax(0,1fr)]'>
          <section aria-label='Waiting for a person' className='flex flex-col gap-2 self-start rounded-[20px] bg-k-f7f7f7 p-3'>
            <h3 className='m-0 flex items-center gap-2 px-1 text-sm font-bold text-k-202020'>
              Waiting for a person
              <span className={cn('rounded-full px-1.5 text-xs', queue.length ? 'bg-k-fdecea text-k-b3261e' : 'bg-k-e9e9e9 text-k-444444')}>{queue.length}</span>
            </h3>
            {shownQueue.length === 0 ? (
              <p className='m-0 px-1 pb-1 text-[13px] text-k-6b6b6b'>Nobody is waiting.</p>
            ) : (
              shownQueue.map((q) => (
                <Card key={q.id}>
                  <div id={`floor-${q.id}`} />
                  <ChatHead chat={q} since={q.since} nowMs={nowMs} />
                  <span className='text-[13px] text-k-202020'>
                    {q.asked_for_person ? 'Asked for a person' : 'In the queue'}
                    {q.preview ? `: “${q.preview}”` : ''}
                  </span>
                  <div className='mt-1 flex flex-wrap gap-2'>
                    {!teamScoped && <OpenLink id={q.id} />}
                    {canGive && <GiveTo chat={q} people={people} primary />}
                  </div>
                </Card>
              ))
            )}
          </section>
          <section aria-label='With a person' className='flex flex-col gap-2 self-start rounded-[20px] bg-k-f7f7f7 p-3'>
            <h3 className='m-0 flex items-center gap-2 px-1 text-sm font-bold text-k-202020'>
              With a person <span className='rounded-full bg-k-e9e9e9 px-1.5 text-xs text-k-444444'>{withPerson.length}</span>
              <span className='ms-auto text-xs font-normal text-k-6b6b6b'>who’s been waiting longest first</span>
            </h3>
            {shownWith.length === 0 ? (
              <p className='m-0 px-1 pb-1 text-[13px] text-k-6b6b6b'>{withPerson.length ? 'No problems here.' : 'Nobody has a chat right now.'}</p>
            ) : (
              <div className='grid grid-cols-1 gap-2 md:grid-cols-2'>
                {shownWith.map((c) => {
                  const line = withPersonLine(c, nowMs)
                  return (
                    <Card key={c.id}>
                      <div id={`floor-${c.id}`} />
                      <ChatHead chat={c} since={c.since} nowMs={nowMs} neutral={!c.client_waiting} />
                      <span className={cn('text-[13px]', line.tone === 'bad' ? 'font-bold text-k-b3261e' : line.tone === 'warn' ? 'text-k-8a4b00' : 'text-k-202020')}>
                        {line.text}
                      </span>
                      <div className='mt-1 flex items-center gap-2'>
                        <PersonAvatar name={c.agent_name ?? '?'} size={30} />
                        <span className='flex-1' />
                        <OpenLink id={c.id} />
                        {canGive && <GiveTo chat={c} people={people} />}
                      </div>
                    </Card>
                  )
                })}
              </div>
            )}
          </section>
          <section aria-label='Only the AI' className='flex flex-col gap-2 self-start rounded-[20px] bg-k-f3f0ff p-3'>
            <h3 className='m-0 flex items-center gap-2 px-1 text-sm font-bold text-k-2c2266'>
              Only the AI <span className='rounded-full bg-k-efeaff px-1.5 text-xs text-k-4a36c4'>{d.ai_only?.count ?? 0}</span>
            </h3>
            <p className='m-0 px-1 text-[13px] text-k-4a36c4'>
              Answering on its own. A chat moves left when the client asks for a person or leaves a contact.
            </p>
            {(d.ai_only?.chats ?? []).slice(0, 6).map((c) => (
              <Card key={c.id} className='gap-0.5'>
                <div className='flex items-baseline justify-between gap-2'>
                  <strong className='min-w-0 truncate text-sm text-k-202020'>{c.client || (c.chat_number ? `#${c.chat_number}` : 'A client')}</strong>
                  <span className='text-xs text-k-6b6b6b'>{c.since ? new Date(c.since).toTimeString().slice(0, 5) : ''}</span>
                </div>
                <span className='text-xs text-k-6b6b6b'>{[c.route, c.tunnel === 'support' ? 'Support' : 'Sales'].filter(Boolean).join(' · ')}</span>
              </Card>
            ))}
            {!teamScoped && (d.ai_only?.count ?? 0) > 0 && (
              <Link to='/chats' className='px-1 text-[13px] font-bold text-k-5b45e0 underline'>
                All {d.ai_only?.count} in Conversations
              </Link>
            )}
          </section>
        </div>
      ) : (
        <div className='grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3'>
          {people.length === 0 ? (
            <p className='m-0 text-sm text-k-555555'>Nobody in this team takes chats.</p>
          ) : (
            people.map((p) => {
              const mine = withPerson.filter((c) => c.agent_id === p.id)
              return (
                <section key={p.id} className='flex flex-col gap-2 rounded-[20px] p-4 ring-1 ring-k-ececec'>
                  <button type='button' onClick={() => onPerson(p.id)} className='flex items-center gap-3 text-start'>
                    <PersonAvatar name={p.name ?? '?'} size={36} />
                    <span className='min-w-0 flex-1'>
                      <strong className='block truncate text-[15px] text-k-202020'>{p.name}</strong>
                      <span className={cn('flex items-center gap-1.5 text-xs', PRESENCE[p.presence].text)}>
                        <span aria-hidden='true' className={cn('size-2 rounded-full', PRESENCE[p.presence].dot)} />
                        {PRESENCE[p.presence].label}
                        {p.team_name ? <span className='text-k-6b6b6b'>· {p.team_name}</span> : null}
                      </span>
                    </span>
                  </button>
                  <p className='m-0 text-[13px] text-k-555555'>
                    Today: {plural(p.took_today, 'chat')} taken, first reply {secondsText(p.first_reply_today_s)}
                    {p.went_back_today ? `, ${p.went_back_today} back to the assistant` : ''}.
                  </p>
                  {mine.map((c) => {
                    const line = withPersonLine(c, nowMs)
                    return (
                      <div key={c.id} className='flex items-center gap-2 rounded-xl bg-k-f7f7f7 px-3 py-2 text-[13px]'>
                        <span className='min-w-0 flex-1 truncate text-k-202020'>{c.client || `#${c.chat_number}`}</span>
                        <span className={cn('truncate', line.tone === 'bad' ? 'font-bold text-k-b3261e' : 'text-k-555555')}>{timer(c.since, nowMs)}</span>
                        <OpenLink id={c.id} />
                      </div>
                    )
                  })}
                </section>
              )
            })
          )}
        </div>
      )}
    </div>
  )
}
