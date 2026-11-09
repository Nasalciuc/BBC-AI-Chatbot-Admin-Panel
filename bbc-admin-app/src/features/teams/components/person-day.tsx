import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { toast } from 'sonner'
import { type ApiError, assignUserToTeam, getPersonDay, updateUser } from '@/lib/api'
import type { Team } from '@/lib/bbc/types'
import { cn } from '@/lib/utils'
import { usePanelModeStore } from '@/stores/panel-mode-store'
import { Sheet, SheetContent, SheetDescription, SheetTitle } from '@/components/ui/sheet'
import { Switch } from '@/components/ui/switch'
import { PersonAvatar } from '@/components/person-avatar'
import { ROLE_LABEL } from '@/features/users/view'
import { PRESENCE, activityText, dayTiles, minutesText, presenceLine, readyBars, shiftText } from '../view'

const h3 = 'm-0 text-[11px] font-bold tracking-[0.05em] text-k-6b6b6b uppercase'
const SHOWN = 8

/**
 * One person's day, from Team: today's numbers, when they were Ready, hours
 * online for a week, what they did today, and where they work.
 */
export function PersonDayPanel({
  userId,
  onClose,
  teams,
  canMoveTeams,
  canManageAccess,
  chatEnabled,
  nowMs,
}: {
  userId: string | null
  onClose: () => void
  teams: Team[]
  /** Owner, admin, dev, a PM for their teams. */
  canMoveTeams: boolean
  /** Owner, admin, dev: "Receives chats", and the link to Users & access. */
  canManageAccess: boolean
  /** From the users list, when known. */
  chatEnabled?: boolean
  nowMs: number
}) {
  const qc = useQueryClient()
  const [wide, setWide] = useState(false)
  const [all, setAll] = useState(false)
  const dormant = usePanelModeStore((s) => s.dormant)
  const q = useQuery({
    queryKey: ['person-day', userId],
    queryFn: () => getPersonDay(userId!),
    enabled: !!userId,
    refetchInterval: dormant ? false : 30_000,
  })
  const d = q.data
  const move = useMutation({
    mutationFn: (teamId: string | null) => assignUserToTeam(userId!, teamId),
    onSuccess: () => {
      toast.success('Team changed.')
      qc.invalidateQueries({ queryKey: ['person-day', userId] })
      qc.invalidateQueries({ queryKey: ['team-now'] })
      qc.invalidateQueries({ queryKey: ['teams-users'] })
    },
    onError: (e) => toast.error((e as ApiError)?.message ?? 'Couldn’t change the team. Try again.'),
  })
  const chats = useMutation({
    mutationFn: (v: boolean) => updateUser(userId!, { chat_enabled: v }),
    onSuccess: (_r, v) => {
      toast.success(v ? 'Receives chats again.' : 'No longer receives chats.')
      qc.invalidateQueries({ queryKey: ['teams-users'] })
    },
    onError: (e) => toast.error((e as ApiError)?.message ?? 'Couldn’t change it. Try again.'),
  })

  const u = d?.user
  const first = (u?.name ?? '').split(' ')[0] || 'They'
  const pres = u ? presenceLine(u) : null
  const maxWeek = Math.max(60, ...(d?.week_online ?? []).map((w) => w.minutes_online))
  const activity = d ? (all ? d.activity : d.activity.slice(0, SHOWN)) : []
  const operator = u?.role === 'sales' || u?.role === 'support'

  return (
    <Sheet open={!!userId} onOpenChange={(o) => !o && onClose()}>
      <SheetContent className={cn('w-full gap-0 overflow-y-auto bg-k-ffffff p-0 sm:max-w-none [&>button]:z-20', wide ? 'sm:w-[760px]' : 'sm:w-[480px]')}>
        <div className='sticky top-0 z-10 flex items-start gap-3 border-b border-k-ececec bg-k-ffffff px-6 pt-5 pb-4 pe-14'>
          <span className='relative'>
            <PersonAvatar name={u?.name ?? '?'} url={u?.avatar_url} size={52} />
            {u && <span aria-hidden='true' className={cn('absolute -end-0.5 -bottom-0.5 size-3.5 rounded-full border-2 border-k-ffffff', PRESENCE[u.presence].dot)} />}
          </span>
          <div className='min-w-0 flex-1'>
            <SheetTitle className='m-0 truncate text-[22px] font-bold text-k-202020'>{u?.name ?? 'Loading…'}</SheetTitle>
            <SheetDescription className='m-0 text-[13px] text-k-555555'>
              {u ? [ROLE_LABEL[u.role as keyof typeof ROLE_LABEL] ?? u.role, u.team?.name, u.team ? shiftText(u.team.shift_start, u.team.shift_end) : 'No team'].filter(Boolean).join(' · ') : ''}
            </SheetDescription>
            {pres && <p className={cn('m-0 mt-0.5 text-[13px] font-bold', pres.tone)}>{pres.text}</p>}
          </div>
          <button
            type='button'
            onClick={() => setWide((w) => !w)}
            className='hidden h-9 flex-none rounded-full border border-k-c9c9c9 px-3.5 text-[13px] font-bold text-k-202020 sm:block'
          >
            {wide ? 'Narrower' : 'Wider'}
          </button>
        </div>

        {q.isError ? (
          <p role='alert' className='m-6 text-sm text-k-8a4b00'>Couldn’t load this person’s day.</p>
        ) : !d ? (
          <div className='m-6 h-64 animate-pulse rounded-2xl bg-k-f7f7f7' aria-busy='true' />
        ) : (
          <div className='flex flex-col gap-6 px-6 py-5'>
            {operator && (
              <section aria-label='Today' className='flex flex-col gap-2'>
                <h3 className={h3}>Today</h3>
                <div className='grid grid-cols-2 gap-2 sm:grid-cols-4'>
                  {dayTiles(d).map((t) => (
                    <div key={t.label} className='flex flex-col gap-0.5 rounded-[14px] bg-k-f7f7f7 px-3 py-2.5'>
                      <span className={cn('text-lg font-bold', t.tone === 'bad' ? 'text-k-b3261e' : t.tone === 'warn' ? 'text-k-8a4b00' : 'text-k-202020')}>{t.value}</span>
                      <span className='text-xs leading-[1.2] text-k-555555'>{t.label}</span>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {operator && (
              <section aria-label='Ready today' className='flex flex-col gap-2'>
                <h3 className={h3}>Ready today</h3>
                <div className='relative h-7 overflow-hidden rounded-lg bg-k-f2f2f2'>
                  {readyBars(d, nowMs).map((b, i) => (
                    <span key={i} className='absolute inset-y-0 bg-k-00a645' style={{ left: `${b.left}%`, width: `${b.width}%` }} />
                  ))}
                  <span
                    aria-hidden='true'
                    className='absolute inset-y-0 w-0.5 bg-kb-202020'
                    style={{ left: `${Math.min(100, ((nowMs - new Date(d.day_start).getTime()) / 86_400_000) * 100)}%` }}
                  />
                </div>
                <div className='flex justify-between text-[11px] text-k-6b6b6b'>
                  {['00', '06', '12', '18', '24'].map((h) => (
                    <span key={h}>{h}</span>
                  ))}
                </div>
                <p className='m-0 text-xs text-k-6b6b6b'>Green: Ready. The line is now. Your time zone.</p>
              </section>
            )}

            <section aria-label='Hours online' className='flex flex-col gap-2'>
              <h3 className={h3}>Last 7 days · hours online</h3>
              <div className='flex h-[110px] items-end gap-2'>
                {d.week_online.map((w, i) => {
                  const today = i === d.week_online.length - 1
                  const label = today ? 'Today' : new Date(`${w.date}T12:00:00`).toLocaleDateString('en-GB', { weekday: 'short' })
                  return (
                    <div key={w.date} className='flex flex-1 flex-col items-center gap-1' title={`${label}: ${minutesText(w.minutes_online)}`}>
                      <span className='text-[11px] text-k-555555'>{w.minutes_online ? (w.minutes_online / 60).toFixed(1) : '·'}</span>
                      <span
                        className={cn('w-full rounded-md', w.minutes_online ? 'bg-kb-202020' : 'bg-k-ececec')}
                        style={{ height: `${Math.max(3, (w.minutes_online / maxWeek) * 70)}px` }}
                      />
                      <span className='text-[11px] text-k-6b6b6b'>{label}</span>
                    </div>
                  )
                })}
              </div>
            </section>

            {operator && (
              <section aria-label='Activity today' className='flex flex-col gap-1'>
                <h3 className={h3}>Activity today</h3>
                {d.activity.length === 0 ? (
                  <p className='m-0 text-sm text-k-555555'>Nothing yet today.</p>
                ) : (
                  <ul className='m-0 flex list-none flex-col p-0'>
                    {activity.map((e, i) => {
                      const t = activityText(e, first)
                      return (
                        <li key={`${e.at}-${i}`} className='flex items-start gap-3 border-t border-k-ececec py-2 text-sm first:border-t-0'>
                          <span className='w-11 flex-none text-k-555555 tabular-nums'>{new Date(e.at).toTimeString().slice(0, 5)}</span>
                          <span
                            aria-hidden='true'
                            className={cn('mt-1.5 size-2 flex-none rounded-full', t.tone === 'ok' ? 'bg-k-00a645' : t.tone === 'bad' ? 'bg-k-d93025' : 'bg-k-9c9c9c')}
                          />
                          <span className='flex-1 text-k-202020'>
                            {e.conversation_id ? (
                              <Link to='/chats' search={{ highlight: e.conversation_id } as never} className='hover:underline'>
                                {t.text}
                              </Link>
                            ) : (
                              t.text
                            )}
                          </span>
                        </li>
                      )
                    })}
                  </ul>
                )}
                {d.activity.length > SHOWN && (
                  <button
                    type='button'
                    onClick={() => setAll((v) => !v)}
                    className='mt-1 h-9 self-start rounded-full border border-k-c9c9c9 px-4 text-[13px] font-bold text-k-202020'
                  >
                    {all ? 'Show less' : `Show the whole day (${d.activity.length})`}
                  </button>
                )}
              </section>
            )}

            <section aria-label='Work' className='flex flex-col gap-2 pb-4'>
              <div className='flex items-baseline justify-between gap-2'>
                <h3 className={h3}>Work</h3>
                {canManageAccess && (
                  <Link to='/users' className='text-[13px] font-bold text-k-5b45e0 hover:underline'>
                    Role, sign-in and deactivation in Users & access
                  </Link>
                )}
              </div>
              <dl className='m-0 grid grid-cols-[120px_1fr] items-center gap-x-3 gap-y-2.5 text-sm'>
                <dt className='text-k-555555'>E-mail</dt>
                <dd className='m-0 break-all text-k-202020'>{u?.email ?? '—'}</dd>
                <dt className='text-k-555555'>Role</dt>
                <dd className='m-0 text-k-202020'>{u ? (ROLE_LABEL[u.role as keyof typeof ROLE_LABEL] ?? u.role) : ''}</dd>
                <dt className='text-k-555555'>Team</dt>
                <dd className='m-0'>
                  {canMoveTeams && operator ? (
                    <select
                      aria-label='Team'
                      value={u?.team?.id ?? ''}
                      disabled={move.isPending}
                      onChange={(e) => move.mutate(e.target.value || null)}
                      className='h-10 w-full max-w-[260px] rounded-xl border border-k-d9d9d9 bg-k-ffffff px-3 text-sm text-k-202020'
                    >
                      <option value=''>No team</option>
                      {teams
                        .filter((t) => t.is_active)
                        .map((t) => (
                          <option key={t.id} value={t.id}>
                            {t.name}
                            {t.shift_start && t.shift_end ? ` · ${t.shift_start.slice(0, 5)}–${t.shift_end.slice(0, 5)}` : ''}
                          </option>
                        ))}
                    </select>
                  ) : (
                    <span className='text-k-202020'>{u?.team?.name ?? 'No team'}</span>
                  )}
                </dd>
                {operator && canManageAccess && (
                  <>
                    <dt className='text-k-555555'>Receives chats</dt>
                    <dd className='m-0 flex items-center gap-2'>
                      <Switch
                        checked={chatEnabled !== false}
                        disabled={chats.isPending || chatEnabled === undefined}
                        onCheckedChange={(v) => chats.mutate(v)}
                        aria-label='Receives chats'
                      />
                      <span className='text-xs text-k-6b6b6b'>Off takes them out of the queue and the routing.</span>
                    </dd>
                  </>
                )}
              </dl>
            </section>
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}
