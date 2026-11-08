import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { ChevronLeft, Lock } from 'lucide-react'
import { getReading } from '@/lib/api'
import type { Reading, ToReadList, ToReadRow } from '@/lib/types'
import { cn } from '@/lib/utils'
import { ChatAvatar } from './components/chat-avatar'
import {
  REASON,
  anchorFor,
  avatarText,
  clientName,
  clockSeconds,
  eventText,
  groupNote,
  groupsOf,
  leadSentence,
  markerOf,
  nextUnread,
  readKey,
  rowLabel,
  rowLine,
  sideTitle,
  weekLine,
} from './to-read-view'
import { clock, dayMonth, initials } from './view'
import type { ChatLayout } from './detail'

const toneText = { bad: 'text-k-b3261e', warn: 'text-k-8a4b00', muted: 'text-k-6b6b6b', ai: 'text-k-4a36c4' } as const
const chipCls = { bad: 'bg-k-fdecea text-k-b3261e', warn: 'bg-k-fff1dc text-k-8a4b00' } as const

function Row({ r, on, unread, onPick }: { r: ToReadRow; on: boolean; unread: boolean; onPick: () => void }) {
  const label = rowLabel(r)
  return (
    <button
      type='button'
      aria-pressed={on}
      onClick={onPick}
      className={cn(
        'flex gap-2.5 rounded-[14px] p-2.5 text-start text-k-202020 focus-visible:outline-2 focus-visible:outline-k-6ea8ff',
        on ? 'bg-k-ffffff shadow-[0_1px_3px_rgba(32,32,32,0.10)]' : 'hover:bg-row-hover'
      )}
    >
      <ChatAvatar text={avatarText(r.client)} tone='bg-k-e6f0fb text-k-1f5fb0' ring='border-k-f7f7f7' />
      <span className='flex min-w-0 flex-1 flex-col gap-[2px]'>
        <span className='flex items-baseline gap-2'>
          <span className={cn('min-w-0 flex-1 truncate text-sm', unread ? 'font-bold' : 'font-normal')}>{clientName(r.client, r.chat_number)}</span>
          <span className='flex-none text-xs text-k-6b6b6b'>{clock(r.at)}</span>
          {unread && <span aria-label='Not read' className='size-2 flex-none rounded-full bg-k-5b45e0' />}
        </span>
        <span className='truncate text-[13px] text-k-555555'>{rowLine(r)}</span>
        <span className='text-xs'>
          <strong className={toneText[label.tone]}>{label.text}</strong>
          {label.more && <span className='text-k-6b6b6b'> · {label.more}</span>}
        </span>
      </span>
    </button>
  )
}

function Group({ title, note, children }: { title: string; note: string; children: React.ReactNode }) {
  return (
    <div className='flex flex-col gap-0.5'>
      <div className='flex items-baseline justify-between gap-2 px-2.5 pt-3 pb-1'>
        <span className='text-[11px] font-bold tracking-[0.05em] text-k-555555 uppercase'>{title}</span>
        <span className='text-[11px] text-k-6b6b6b'>{note}</span>
      </div>
      {children}
    </div>
  )
}

function jump(id: string) {
  const el = document.getElementById(id)
  if (!el) return
  el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  el.classList.add('ring-2', 'ring-k-f2b01e')
  setTimeout(() => el.classList.remove('ring-2', 'ring-k-f2b01e'), 1600)
}

type Line =
  | { k: 'msg'; m: Reading['messages'][number] }
  | { k: 'event'; e: Reading['events'][number] }
  | { k: 'mark'; key: keyof typeof REASON; name: string | null; at: string }

/** Messages and what happened, in time order, with the rules where they broke. */
function linesOf(d: Reading): Line[] {
  const at = (iso: string) => new Date(iso).getTime()
  const out: (Line & { t: number; o: number })[] = []
  // At the same second: what happened, then the rule it broke, then what the
  // client saw (the assistant's "Sorry for the wait" follows the went-back).
  d.messages.forEach((m) => out.push({ k: 'msg', m, t: at(m.at), o: 2 }))
  d.events
    .filter((e) => e.kind !== 'first_reply' && e.kind !== 'went_back')
    .forEach((e) => out.push({ k: 'event', e, t: at(e.at), o: 0 }))
  d.reasons
    .filter((r) => r.key !== 'anyone')
    .forEach((r) => out.push({ k: 'mark', key: r.key, name: r.name, at: r.at, t: at(r.at), o: 1 }))
  return out.sort((a, b) => a.t - b.t || a.o - b.o)
}

function Reader({ row, onRead, isRead, wide }: { row: ToReadRow; onRead: () => void; isRead: boolean; wide: boolean }) {
  const q = useQuery({ queryKey: ['reading', row.id], queryFn: () => getReading(row.id) })
  const d = q.data
  const side = sideTitle(row)
  const anyoneAt = d?.reasons.find((r) => r.key === 'anyone')?.at
  const firstReply = new Map((d?.events ?? []).filter((e) => e.kind === 'first_reply').map((e) => [e.user_id, e]))
  const shownReply = new Set<string>()

  const body = !d ? (
    q.isError ? (
      <p role='alert' className='m-0 p-6 text-sm text-k-8a4b00'>Couldn’t load this chat. It may not be your team’s.</p>
    ) : (
      <div className='m-6 h-64 animate-pulse rounded-2xl bg-k-f7f7f7' aria-busy='true' />
    )
  ) : (
    <div className='flex flex-col gap-3 px-5 py-4'>
      {linesOf(d).map((l, i) => {
        if (l.k === 'event') {
          const t = eventText(l.e, d.client)
          return (
            <div key={`e${i}`} className='flex items-center gap-3 text-xs text-k-6b6b6b'>
              <span className='h-px flex-1 bg-k-ececec' />
              <span>
                {t.text} · {clock(l.e.at)}
              </span>
              <span className='h-px flex-1 bg-k-ececec' />
            </div>
          )
        }
        if (l.k === 'mark') {
          const m = markerOf(l.key, l.name)
          return (
            <div
              key={`m${i}`}
              id={anchorFor(l.key)}
              className={cn('flex items-start gap-2.5 rounded-2xl px-3.5 py-2.5 text-[13px] ring-1', m.tone === 'bad' ? 'bg-k-fde8e6 ring-k-f0cdc8' : 'bg-k-fffaf2 ring-k-f2b01e')}
            >
              <span aria-hidden='true' className={cn('mt-1.5 size-2 flex-none rounded-full', m.tone === 'bad' ? 'bg-k-d93025' : 'bg-k-d97a00')} />
              <span className='flex-1 text-k-202020'>
                <strong className={m.tone === 'bad' ? 'text-k-b3261e' : 'text-k-8a4b00'}>{m.title}</strong> {m.text}
              </span>
              <span className='text-xs text-k-6b6b6b'>{clock(l.at)}</span>
            </div>
          )
        }
        const m = l.m
        if (m.role === 'system') {
          return (
            <p key={m.id ?? `s${i}`} className='m-0 text-center text-xs text-k-6b6b6b'>
              The client saw: “{m.text}”
            </p>
          )
        }
        const who = m.role === 'user' ? d.client || 'The client' : m.role === 'ai' ? 'Assistant' : (m.name ?? 'Agent')
        const reply = m.role === 'agent' && m.user_id && !shownReply.has(m.user_id) ? firstReply.get(m.user_id) : undefined
        if (reply && m.user_id) shownReply.add(m.user_id)
        const asked = m.role === 'user' && anyoneAt === m.at
        return (
          <div key={m.id ?? `m${i}`} id={asked ? anchorFor('anyone') : undefined} className={cn('flex gap-3 rounded-2xl', asked && 'bg-k-fdecea p-2')}>
            <ChatAvatar
              text={m.role === 'ai' ? 'AI' : m.role === 'user' ? avatarText(d.client) : initials(m.name)}
              tone={m.role === 'ai' ? 'bg-k-efeaff text-k-4a36c4' : m.role === 'user' ? 'bg-k-e6f0fb text-k-1f5fb0' : 'bg-kb-202020 text-on-btn'}
              size={32}
            />
            <div className='min-w-0 flex-1'>
              <p className='m-0 text-[13px]'>
                <strong className='text-k-202020'>{who}</strong> <span className='text-k-6b6b6b'>{clock(m.at)}</span>
                {reply && <span className='text-k-6b6b6b'> · first reply in {reply.seconds} s</span>}
                {asked && <strong className='ms-1 text-k-b3261e'>“Anyone there?”</strong>}
              </p>
              <p className='m-0 text-[15px] leading-[1.5] break-words whitespace-pre-wrap text-k-202020'>{m.text}</p>
            </div>
          </div>
        )
      })}
    </div>
  )

  const sidePanel = (
    <div className='flex flex-col gap-3'>
      <section className='flex flex-col gap-2 rounded-[18px] bg-k-ffffff p-4'>
        <span className='text-[11px] font-bold tracking-[0.05em] text-k-6b6b6b uppercase'>{side.kicker}</span>
        <p className='m-0 text-[17px] leading-[1.35] font-bold text-k-202020'>{side.title}</p>
        {row.kind === 'lost' && (
          <ul className='m-0 flex list-none flex-col gap-1 p-0'>
            {row.reasons.map((r) => (
              <li key={r.key} className='flex items-center gap-2 text-sm'>
                <span aria-hidden='true' className={cn('size-2 rounded-full', REASON[r.key].tone === 'bad' ? 'bg-k-d93025' : 'bg-k-d97a00')} />
                <span className='flex-1 text-k-202020'>{REASON[r.key].label}</span>
                <button type='button' onClick={() => jump(anchorFor(r.key))} className='text-[13px] font-bold text-k-5b45e0 hover:underline'>
                  Jump
                </button>
              </li>
            ))}
          </ul>
        )}
        <p className='m-0 text-xs leading-[1.45] text-k-6b6b6b'>{side.why}</p>
      </section>
      {d && (
        <section className='flex flex-col gap-1.5 rounded-[18px] bg-k-ffffff p-4'>
          <span className='text-[11px] font-bold tracking-[0.05em] text-k-6b6b6b uppercase'>What happened</span>
          <ul className='m-0 flex list-none flex-col p-0'>
            {d.events.map((e, i) => {
              const t = eventText(e, d.client)
              return (
                <li key={i} className='flex gap-3 py-1 text-[13px]'>
                  <span className='w-[64px] flex-none text-k-6b6b6b tabular-nums'>{clockSeconds(e.at)}</span>
                  <span className={cn('flex-1', t.bad ? 'font-bold text-k-b3261e' : 'text-k-202020')}>{t.text}</span>
                  {e.kind === 'went_back' && (
                    <button type='button' onClick={() => jump(anchorFor('back'))} className='text-xs font-bold text-k-5b45e0 hover:underline'>
                      Jump
                    </button>
                  )}
                </li>
              )
            })}
          </ul>
        </section>
      )}
      {d?.lead && (
        <section className='flex flex-col gap-2 rounded-[18px] bg-k-ffffff p-4'>
          <span className='text-[11px] font-bold tracking-[0.05em] text-k-6b6b6b uppercase'>Trip and lead</span>
          <p className='m-0 text-xl font-bold text-k-202020'>{d.lead.route ?? 'No route yet'}</p>
          <dl className='m-0 grid grid-cols-[72px_1fr] gap-x-3 gap-y-1.5 text-[13px]'>
            <dt className='text-k-6b6b6b'>Lead</dt>
            <dd className='m-0 text-k-202020'>{leadSentence(d.lead)}</dd>
            {(d.lead.phone || d.lead.email) && (
              <>
                <dt className='text-k-6b6b6b'>Contact</dt>
                <dd className='m-0 text-k-202020'>
                  {[d.lead.phone, d.lead.email].filter(Boolean).join(' · ')}
                  {d.client.startsWith('•') && <span className='block text-k-6b6b6b'>Hidden from supervisors.</span>}
                </dd>
              </>
            )}
          </dl>
        </section>
      )}
      {d?.week && (
        <section className='flex flex-col gap-1.5 rounded-[18px] bg-k-ffffff p-4'>
          <span className='text-[11px] font-bold tracking-[0.05em] text-k-6b6b6b uppercase'>{d.week.name ?? 'The agent'} this week</span>
          <p className='m-0 text-[13px] text-k-202020'>{weekLine(d.week)}</p>
          <Link to='/' search={{ tab: 'agents' } as never} className='text-[13px] font-bold text-k-5b45e0 hover:underline'>
            See {d.week.name?.split(' ')[0] ?? 'them'} in Dashboard → Agents
          </Link>
        </section>
      )}
    </div>
  )

  return (
    <div className='flex min-h-0 min-w-0 flex-1 gap-4'>
      <section aria-label='Chat to read' className='flex min-w-0 flex-1 flex-col overflow-hidden rounded-[20px] bg-k-ffffff shadow-[0_0_0_1px_var(--k-ececec),0_8px_24px_rgba(32,32,32,0.04)]'>
        <header className='flex flex-col gap-2 border-b border-k-ececec px-5 pt-4 pb-3'>
          <div className='flex items-center gap-3'>
            <ChatAvatar text={avatarText(row.client)} tone='bg-k-e6f0fb text-k-1f5fb0' size={42} />
            <div className='min-w-0 flex-1'>
              <p className='m-0 truncate text-lg font-bold text-k-202020'>{clientName(row.client, row.chat_number)}</p>
              <p className='m-0 truncate text-[13px] text-k-555555'>
                {row.agent_name ?? 'Nobody'}
                {d?.closed_at ? ` · Closed ${clock(d.closed_at)}` : d?.status ? ` · ${d.status === 'closed' ? 'Closed' : 'Open'}` : ''}
                {row.chat_number && !row.client.startsWith('•') ? ` · #${row.chat_number}` : ''}
                {d?.created_at ? ` · ${dayMonth(d.created_at.slice(0, 10))}` : ''}
              </p>
            </div>
          </div>
          {row.kind === 'lost' && (
            <div className='flex flex-wrap gap-1.5'>
              {row.reasons.map((r) => (
                <button
                  key={r.key}
                  type='button'
                  onClick={() => jump(anchorFor(r.key))}
                  className={cn('inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-bold', chipCls[REASON[r.key].tone])}
                >
                  <span aria-hidden='true' className={cn('size-1.5 rounded-full', REASON[r.key].tone === 'bad' ? 'bg-k-d93025' : 'bg-k-d97a00')} />
                  {REASON[r.key].label}
                </button>
              ))}
            </div>
          )}
        </header>
        <div className='min-h-0 flex-1 overflow-y-auto'>
          {body}
          {!wide && <div className='px-5 pb-5'>{sidePanel}</div>}
        </div>
        <footer className='flex items-center justify-between gap-3 border-t border-k-ececec px-5 py-3'>
          <span className='flex items-center gap-1.5 text-xs text-k-6b6b6b'>
            <Lock className='size-3.5' /> Read-only. “Read” is kept in this browser.
          </span>
          <button
            type='button'
            onClick={onRead}
            disabled={isRead}
            className='h-11 rounded-full bg-kb-202020 px-5 text-sm font-bold text-on-btn disabled:opacity-50'
          >
            {isRead ? 'Read' : 'Mark read'}
          </button>
        </footer>
      </section>
      {wide && <aside className='flex w-[340px] flex-none flex-col overflow-y-auto rounded-[20px] bg-k-f7f7f7 p-3'>{sidePanel}</aside>}
    </div>
  )
}

/**
 * To read (supervisor, owner, admin, dev): this week's lost chats by fixed
 * rules and last week's sample of each person's chats. Read-only; "Mark read"
 * is kept in this browser.
 */
export function ToRead({ layout, tabBar, list, isError, onRetry, read, markRead }: {
  layout: ChatLayout
  tabBar: React.ReactNode
  list: ToReadList | undefined
  isError: boolean
  onRetry: () => void
  /** Kept by the page, so the tab's count follows "Mark read". */
  read: Set<string>
  markRead: (key: string) => void
}) {
  const [who, setWho] = useState('all')
  const [openId, setOpenId] = useState<string | null>(null)
  const [nowMs] = useState(() => Date.now())

  const groups = useMemo(() => (list ? groupsOf(list, who, nowMs) : { today: [], earlier: [], sample: [] }), [list, who, nowMs])
  const ordered = [...groups.today, ...groups.earlier, ...groups.sample]
  const open = ordered.find((r) => r.id === openId) ?? null
  const isRead = (r: ToReadRow) => !!list && read.has(readKey(list, r))

  const markAndNext = () => {
    if (!list || !open) return
    markRead(readKey(list, open))
    const next = nextUnread(ordered, list, new Set([...read, readKey(list, open)]), open.id)
    setOpenId(next?.id ?? open.id)
  }

  const scopeNote = list?.no_team
    ? 'You’re not in a team yet. Ask the owner to make you a team’s supervisor in Team › Teams & shifts.'
    : list && list.people.length && list.lost.concat(list.sample).some((r) => r.client.startsWith('•'))
      ? 'Your team only. Phone and e-mail are hidden.'
      : 'Every person’s lost chats and last week’s sample.'

  const narrowOpen = layout === 'narrow' && !!open
  return (
    <div className={cn('flex min-h-0 flex-1', layout === 'narrow' ? 'gap-0' : 'gap-4')}>
      <section
        aria-label='To read'
        className={cn(
          'flex min-w-0 flex-col overflow-hidden',
          layout === 'narrow' ? 'flex-1 bg-k-ffffff' : 'w-[320px] flex-none rounded-[20px] bg-k-f7f7f7',
          narrowOpen && 'hidden'
        )}
      >
        <div className={cn('flex flex-col gap-2.5 pb-1', layout === 'narrow' ? 'px-0 pt-1' : 'px-3.5 pt-3.5')}>
          {tabBar}
          <label className='flex h-10 items-center gap-2 rounded-xl bg-k-ffffff px-3 text-[13px] text-k-555555 shadow-[inset_0_0_0_1px_var(--k-e3e2e2)]'>
            Whose chats
            <select value={who} onChange={(e) => setWho(e.target.value)} className='min-w-0 flex-1 bg-transparent text-sm font-bold text-k-202020'>
              <option value='all'>Everyone</option>
              {(list?.people ?? []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name ?? 'Someone'}
                </option>
              ))}
            </select>
          </label>
          <p className='m-0 px-1 text-xs leading-[1.4] text-k-6b6b6b'>{scopeNote}</p>
        </div>
        <div className='flex min-h-0 flex-1 flex-col overflow-y-auto px-2 pb-3'>
          {isError ? (
            <div role='alert' className='m-1.5 flex flex-col gap-2 rounded-[14px] bg-k-fdecea p-4 text-[13px] text-k-b3261e'>
              <strong>Couldn’t load the chats to read.</strong>
              <button type='button' onClick={onRetry} className='self-start font-bold underline'>
                Try again
              </button>
            </div>
          ) : !list ? (
            <div className='p-4 text-sm text-k-6b6b6b'>Loading…</div>
          ) : ordered.length === 0 ? (
            <div className='m-1.5 rounded-[14px] bg-k-ffffff p-[18px] text-sm leading-normal text-k-555555'>
              {list.no_team
                ? 'Nothing to read until you have a team.'
                : 'No lost chats this week, and no sample yet. New lost chats show up here as they happen; the sample comes on Monday: 2 chats of each person’s, and one more for anyone who needs help.'}
            </div>
          ) : (
            <>
              {(groups.today.length > 0 || groups.earlier.length === 0) && (
                <Group title='Lost' note={groupNote(groups.today, list, read, 'today')}>
                  {groups.today.length === 0 ? (
                    <p className='m-0 px-2.5 pb-1 text-[13px] text-k-6b6b6b'>Nothing lost today.</p>
                  ) : (
                    groups.today.map((r) => <Row key={`l${r.id}`} r={r} on={openId === r.id} unread={!isRead(r)} onPick={() => setOpenId(r.id)} />)
                  )}
                </Group>
              )}
              {groups.earlier.length > 0 && (
                <Group title={groups.today.length ? 'Earlier this week' : 'Lost this week'} note={groupNote(groups.earlier, list, read)}>
                  {groups.earlier.map((r) => (
                    <Row key={`e${r.id}`} r={r} on={openId === r.id} unread={!isRead(r)} onPick={() => setOpenId(r.id)} />
                  ))}
                </Group>
              )}
              <Group title='From your team · last week' note={groupNote(groups.sample, list, read, `${groups.sample.length} picked`)}>
                {groups.sample.length === 0 ? (
                  <p className='m-0 px-2.5 pb-1 text-[13px] text-k-6b6b6b'>Nobody answered a chat last week.</p>
                ) : (
                  groups.sample.map((r) => <Row key={`s${r.id}`} r={r} on={openId === r.id} unread={!isRead(r)} onPick={() => setOpenId(r.id)} />)
                )}
              </Group>
            </>
          )}
        </div>
      </section>
      {open ? (
        <div className='flex min-w-0 flex-1 flex-col gap-2'>
          {layout === 'narrow' && (
            <button type='button' onClick={() => setOpenId(null)} className='flex items-center gap-1 self-start px-2 py-2 text-sm font-bold text-k-202020'>
              <ChevronLeft className='size-4' /> To read
            </button>
          )}
          <Reader row={open} onRead={markAndNext} isRead={isRead(open)} wide={layout === 'wide'} />
        </div>
      ) : (
        layout !== 'narrow' && (
          <section className='flex min-w-0 flex-1 flex-col items-center justify-center gap-2.5 rounded-[20px] bg-k-ffffff p-10 text-center shadow-[0_0_0_1px_var(--k-ececec),0_8px_24px_rgba(32,32,32,0.04)]'>
            <div className='text-lg font-bold text-k-202020'>Pick a chat to read</div>
            <div className='max-w-[400px] text-sm leading-normal text-k-555555'>
              Lost chats come from fixed rules, no AI: went back to the assistant, “Anyone there?”, never wrote, after a YES. The sample is 2
              chats of each person’s from last week, picked at random.
            </div>
          </section>
        )
      )}
    </div>
  )
}
