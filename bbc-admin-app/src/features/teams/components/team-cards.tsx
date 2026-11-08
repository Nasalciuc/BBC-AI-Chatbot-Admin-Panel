import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { toast } from 'sonner'
import { type ApiError, updateTeam } from '@/lib/api'
import type { Team } from '@/lib/bbc/types'
import type { TeamNow } from '@/lib/types'
import { cn } from '@/lib/utils'
import { type TeamUser } from '../data/types'
import { TeamDeactivateDialog } from './team-deactivate-dialog'
import { TeamDialog } from './team-dialog'
import { TeamMembersDialog } from './team-members-dialog'
import { coverageCell, coverageNote, plural, shiftText } from '../view'

type Card = NonNullable<TeamNow['teams']>[number]

const btn = 'h-10 rounded-full px-4 text-sm font-bold focus-visible:outline-2 focus-visible:outline-k-6ea8ff'

function Coverage({ cov }: { cov: (number | null)[] }) {
  return (
    <div className='flex flex-col gap-1.5'>
      <div className='flex items-baseline gap-2'>
        <span className='text-[11px] font-bold tracking-[0.05em] text-k-555555 uppercase'>Ready today</span>
        <span className='text-xs text-k-6b6b6b'>{coverageNote(cov)}</span>
      </div>
      <div className='flex h-6 gap-px overflow-hidden rounded-md' role='img' aria-label={`People Ready each hour today: ${cov.map((n) => (n === null ? '–' : n)).join(', ')}`}>
        {cov.map((n, h) => (
          <span key={h} title={`${String(h).padStart(2, '0')}:00 · ${n === null ? 'not yet' : plural(n, 'person', 'people') + ' Ready'}`} className={cn('flex-1', coverageCell(n))} />
        ))}
      </div>
      <div className='flex justify-between text-[11px] text-k-6b6b6b'>
        {['00', '06', '12', '18', '24'].map((h) => (
          <span key={h}>{h}</span>
        ))}
      </div>
    </div>
  )
}

/**
 * Teams & shifts: each team, who leads it, who's in it, today, and how many
 * of its people were Ready each hour. Creating, editing, people and
 * deactivating use the same dialogs as before.
 */
export function TeamCards({
  teams,
  cards,
  users,
  canManage,
  onSeePeople,
}: {
  teams: Team[]
  cards: Card[]
  users: TeamUser[]
  canManage: boolean
  onSeePeople: (teamId: string) => void
}) {
  const qc = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)
  const [editTeam, setEditTeam] = useState<Team | null>(null)
  const [membersTeam, setMembersTeam] = useState<Team | null>(null)
  const [deactivateTeam, setDeactivateTeam] = useState<Team | null>(null)
  const byId = new Map(cards.map((c) => [c.id, c]))
  const name = (id?: string | null) => (id ? (users.find((u) => u.id === id)?.name ?? null) : null)
  const supervisors = users.filter((u) => u.role === 'supervisor')
  const pms = users.filter((u) => u.role === 'project_manager')
  const reactivate = useMutation({
    mutationFn: (t: Team) => updateTeam(t.id, { is_active: true }),
    onSuccess: (_r, t) => {
      toast.success(`${t.name} is active again.`)
      qc.invalidateQueries({ queryKey: ['teams'] })
      qc.invalidateQueries({ queryKey: ['team-now'] })
    },
    onError: (e) => toast.error((e as ApiError)?.message ?? 'Couldn’t reactivate the team. Try again.'),
  })
  const sorted = [...teams].sort((a, b) => Number(b.is_active) - Number(a.is_active) || a.name.localeCompare(b.name))

  return (
    <>
      <div className='grid grid-cols-1 gap-3.5 xl:grid-cols-2'>
        {sorted.map((t) => {
          const c = byId.get(t.id)
          const members = c?.members ?? []
          return (
            <section key={t.id} aria-label={t.name} className={cn('flex flex-col gap-3 rounded-[20px] p-5 ring-1 ring-k-ececec', !t.is_active && 'opacity-70')}>
              <div className='flex items-start justify-between gap-2'>
                <div>
                  <h2 className='m-0 text-xl font-bold text-k-202020'>
                    {t.name}
                    {!t.is_active && ' (inactive)'}
                  </h2>
                  <p className='m-0 text-[13px] text-k-555555'>{shiftText(t.shift_start, t.shift_end)}</p>
                </div>
                {!t.is_active && <span className='rounded-full bg-k-f2f2f2 px-2.5 py-0.5 text-xs font-bold text-k-555555'>Inactive</span>}
              </div>
              <dl className='m-0 grid grid-cols-[110px_1fr] gap-x-3 gap-y-1.5 text-sm'>
                <dt className='text-k-555555'>Supervisor</dt>
                <dd className='m-0 text-k-202020'>{c?.supervisor_name ?? name(t.supervisor_id) ?? 'No supervisor yet'}</dd>
                <dt className='text-k-555555'>Manager</dt>
                <dd className='m-0 text-k-202020'>{c?.pm_name ?? name(t.pm_id) ?? '—'}</dd>
                <dt className='text-k-555555'>People</dt>
                <dd className='m-0 text-k-202020'>
                  {members.length
                    ? `${members.length} · ${members.map((m) => (m.name ?? '').split(' ')[0]).join(', ')}${c ? ` · ${c.ready_now} Ready now` : ''}`
                    : 'Nobody yet'}
                </dd>
                <dt className='text-k-555555'>Today</dt>
                <dd className='m-0 text-k-202020'>
                  {c && (c.won_today || c.back_today)
                    ? `${plural(c.won_today, 'chat')} taken · ${c.back_today} back to the assistant`
                    : 'No chats yet today'}
                </dd>
              </dl>
              {t.is_active && c && <Coverage cov={c.coverage} />}
              <div className='flex flex-wrap gap-2'>
                {canManage && (
                  <button type='button' onClick={() => setEditTeam(t)} className={cn(btn, 'border border-kl-202020 text-k-202020')}>
                    Edit
                  </button>
                )}
                <button
                  type='button'
                  onClick={() => (canManage ? setMembersTeam(t) : onSeePeople(t.id))}
                  className={cn(btn, 'border border-k-c9c9c9 text-k-202020')}
                >
                  {canManage ? 'People in it' : 'See people'}
                </button>
                {canManage &&
                  (t.is_active ? (
                    <button type='button' onClick={() => setDeactivateTeam(t)} className={cn(btn, 'text-k-b3261e')}>
                      Deactivate
                    </button>
                  ) : (
                    <button type='button' disabled={reactivate.isPending} onClick={() => reactivate.mutate(t)} className={cn(btn, 'text-k-0a6b35')}>
                      Reactivate
                    </button>
                  ))}
              </div>
            </section>
          )
        })}
        {canManage && (
          <button
            type='button'
            onClick={() => setCreateOpen(true)}
            className='flex min-h-[180px] flex-col items-center justify-center gap-1.5 rounded-[20px] border-2 border-dashed border-k-d9d9d9 p-5 text-center hover:bg-k-f7f7f7'
          >
            <Plus className='size-6 text-k-444444' />
            <strong className='text-base text-k-202020'>New team</strong>
            <span className='text-[13px] text-k-555555'>Name, shift, supervisor and people</span>
          </button>
        )}
      </div>
      <p className='m-0 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-k-555555'>
        <strong className='text-k-202020'>Ready today</strong>
        {[
          ['bg-k-ececec', 'Nobody Ready'],
          ['bg-k-e3f6ea', '1 Ready'],
          ['bg-k-5fd394', '2 Ready'],
          ['bg-k-00a645', '3 or more'],
        ].map(([cls, label]) => (
          <span key={label} className='flex items-center gap-1.5'>
            <span aria-hidden='true' className={cn('size-3 rounded-sm', cls)} />
            {label}
          </span>
        ))}
        <span>Hours are yours; a shift is shown as it was set, with no time zone.</span>
      </p>

      <TeamDialog open={createOpen} onOpenChange={setCreateOpen} supervisors={supervisors} projectManagers={pms} />
      {editTeam && (
        <TeamDialog
          open={!!editTeam}
          onOpenChange={(o) => !o && setEditTeam(null)}
          team={editTeam}
          supervisors={supervisors}
          projectManagers={pms}
        />
      )}
      {membersTeam && (
        <TeamMembersDialog open={!!membersTeam} onOpenChange={(o) => !o && setMembersTeam(null)} team={membersTeam} users={users} />
      )}
      {deactivateTeam && (
        <TeamDeactivateDialog
          open={!!deactivateTeam}
          onOpenChange={(o) => !o && setDeactivateTeam(null)}
          team={deactivateTeam}
          onDone={() => {
            toast.success('Team deactivated')
            setDeactivateTeam(null)
            qc.invalidateQueries({ queryKey: ['team-now'] })
          }}
        />
      )}
    </>
  )
}
