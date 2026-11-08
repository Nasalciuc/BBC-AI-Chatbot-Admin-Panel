import { useMemo, useState } from 'react'
import { Search } from 'lucide-react'
import type { Team } from '@/lib/bbc/types'
import type { FloorPerson, PanelUser } from '@/lib/types'
import { cn } from '@/lib/utils'
import { PersonAvatar } from '@/components/person-avatar'
import { ROLE_LABEL, ROLE_ORDER, lastActive } from '@/features/users/view'
import { rosterStatus } from '../view'

const selectCls = 'h-10 rounded-xl border border-k-d9d9d9 bg-k-ffffff px-3 text-sm text-k-202020'

/** Team · People: who works here, in which team, and how they are right now. */
export function Roster({
  users,
  people,
  teams,
  nowMs,
  onPerson,
  loading,
  failed,
}: {
  users: PanelUser[]
  people: FloorPerson[]
  teams: Team[]
  nowMs: number
  onPerson: (id: string) => void
  loading: boolean
  failed: boolean
}) {
  const [q, setQ] = useState('')
  const [role, setRole] = useState('')
  const [team, setTeam] = useState('')
  const presence = useMemo(() => new Map(people.map((p) => [p.id, p])), [people])
  const teamName = useMemo(() => new Map(teams.map((t) => [t.id, t.name])), [teams])
  const rows = users.filter((u) => {
    const s = q.trim().toLowerCase()
    if (s && !`${u.name ?? ''} ${u.email}`.toLowerCase().includes(s)) return false
    if (role && u.role !== role) return false
    if (team === 'none' ? !!u.team_id : team && u.team_id !== team) return false
    return true
  })

  return (
    <div className='flex flex-col gap-3.5'>
      <div className='flex flex-wrap items-center gap-2'>
        <label className='relative'>
          <span className='sr-only'>Search people</span>
          <Search aria-hidden='true' className='pointer-events-none absolute start-3 top-1/2 size-4 -translate-y-1/2 text-k-6b6b6b' />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder='Search people'
            className='h-10 w-full rounded-xl bg-k-f2f2f2 ps-9 pe-3 text-sm text-k-202020 placeholder:text-k-6b6b6b sm:w-[240px]'
          />
        </label>
        <label className='flex items-center gap-1.5 text-[13px] text-k-555555'>
          Role
          <select value={role} onChange={(e) => setRole(e.target.value)} className={selectCls}>
            <option value=''>All roles</option>
            {ROLE_ORDER.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r]}
              </option>
            ))}
          </select>
        </label>
        <label className='flex items-center gap-1.5 text-[13px] text-k-555555'>
          Team
          <select value={team} onChange={(e) => setTeam(e.target.value)} className={selectCls}>
            <option value=''>All teams</option>
            {teams.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
            <option value='none'>No team</option>
          </select>
        </label>
        <span className='ms-auto text-[13px] text-k-555555'>{rows.length === users.length ? `All ${users.length}` : `${rows.length} of ${users.length}`}</span>
      </div>

      {failed ? (
        <div role='alert' className='rounded-2xl bg-k-fff3e0 px-4 py-3 text-sm text-k-8a4b00'>
          <strong>Couldn’t load the people.</strong> The list isn’t shown rather than shown empty.
        </div>
      ) : loading ? (
        <div className='h-64 animate-pulse rounded-[20px] bg-k-f7f7f7' aria-busy='true' />
      ) : (
        <div className='overflow-x-auto rounded-[20px] ring-1 ring-k-ececec'>
          <table className='w-full min-w-[760px] border-collapse'>
            <thead>
              <tr className='bg-k-f7f7f7'>
                {['Person', 'Role', 'Team', 'Status', 'Last seen'].map((h) => (
                  <th key={h} className='px-4 py-3 text-left text-[11px] font-bold tracking-[0.05em] text-k-555555 uppercase'>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((u) => {
                const st = rosterStatus(u, presence.get(u.id))
                const noTeam = !u.team_id && (u.role === 'sales' || u.role === 'support' || u.role === 'supervisor')
                return (
                  <tr
                    key={u.id}
                    tabIndex={0}
                    onClick={() => onPerson(u.id)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        onPerson(u.id)
                      }
                    }}
                    className='cursor-pointer border-t border-k-ececec hover:bg-row-hover focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-k-6ea8ff'
                  >
                    <td className='px-4 py-3'>
                      <div className='flex items-center gap-3'>
                        <PersonAvatar name={u.name ?? u.email} size={36} />
                        <span className='min-w-0'>
                          <strong className='block truncate text-[15px] text-k-202020'>{u.name ?? u.email}</strong>
                          <span className='block truncate text-xs text-k-555555'>{u.email}</span>
                        </span>
                      </div>
                    </td>
                    <td className='px-4 py-3 text-sm text-k-202020'>{ROLE_LABEL[u.role as keyof typeof ROLE_LABEL] ?? u.role}</td>
                    <td className={cn('px-4 py-3 text-sm', noTeam ? 'text-k-8a4b00' : 'text-k-202020')}>
                      {u.team_id ? (teamName.get(u.team_id) ?? '—') : noTeam ? 'No team' : '—'}
                    </td>
                    <td className='px-4 py-3'>
                      <span className='flex items-center gap-2 text-sm text-k-202020'>
                        <span aria-hidden='true' className={cn('size-2 rounded-full', st.dot)} />
                        {st.label}
                      </span>
                    </td>
                    <td className='px-4 py-3 text-sm whitespace-nowrap text-k-555555'>{lastActive(u.last_seen_at, nowMs)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
