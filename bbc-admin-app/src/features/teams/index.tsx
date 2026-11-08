import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { UsersRound } from 'lucide-react'
import { getPanelUsers, getTeamNow, getTeams } from '@/lib/api'
import type { PanelUser } from '@/lib/types'
import { cn } from '@/lib/utils'
import { useAuthStore } from '@/stores/auth-store'
import { usePanelModeStore } from '@/stores/panel-mode-store'
import { HeaderActions } from '@/components/header-actions'
import { PageHeader } from '@/components/layout/page-header'
import { InviteDialog } from '@/features/users/components/invite-dialog'
import { Floor } from './components/floor'
import { PersonDayPanel } from './components/person-day'
import { Roster } from './components/roster'
import { TeamCards } from './components/team-cards'
import { canSeeTeam, scopeLine } from './view'

type Tab = 'now' | 'people' | 'teams'

const PRIVILEGED = ['owner', 'admin', 'dev']

function NoTeam() {
  return (
    <section className='flex max-w-[620px] flex-col gap-2 rounded-[20px] bg-k-f7f7f7 p-6'>
      <span aria-hidden='true' className='flex size-11 items-center justify-center rounded-full bg-k-ffffff'>
        <UsersRound className='size-5 text-k-444444' />
      </span>
      <h2 className='m-0 text-xl font-bold text-k-202020'>You’re not in a team yet</h2>
      <p className='m-0 text-sm leading-[1.5] text-k-444444'>
        A supervisor sees the chats and the people of their own team, and nothing else. Until the owner puts you in a team, Team, To read and
        Conversations stay empty for you; the Dashboard still shows all the chats of your tunnel.
      </p>
      <p className='m-0 text-sm leading-[1.5] text-k-444444'>Ask the owner to make you a team’s supervisor in Team › Teams & shifts.</p>
    </section>
  )
}

/**
 * Team: the floor right now, the people, and the teams with their shifts.
 * Owner, admin, dev: everyone. Supervisor: their team (read-only, can give
 * chats). Project manager: their teams (builds them, can't give chats).
 */
export function Teams() {
  const role = useAuthStore((s) => s.auth.user?.role ?? '')
  const dormant = usePanelModeStore((s) => s.dormant)
  const privileged = PRIVILEGED.includes(role)
  const canManage = privileged || role === 'project_manager'
  const [tab, setTab] = useState<Tab>('now')
  const [person, setPerson] = useState<string | null>(null)
  const [inviting, setInviting] = useState(false)
  const [nowMs, setNowMs] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNowMs(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  const now = useQuery({
    queryKey: ['team-now'],
    queryFn: getTeamNow,
    enabled: canSeeTeam(role),
    refetchInterval: dormant ? false : 15_000,
  })
  const teams = useQuery({ queryKey: ['teams'], queryFn: () => getTeams(), enabled: canSeeTeam(role) })
  const users = useQuery({
    queryKey: ['teams-users'],
    queryFn: getPanelUsers,
    enabled: canSeeTeam(role),
  })

  const d = now.data
  const myTeams = useMemo(() => (teams.data ?? []).filter((t) => t.is_active).map((t) => t.name), [teams.data])
  // A supervisor's roster is their team (the server scopes it); a PM's, their teams' people.
  const scopedIds = useMemo(() => new Set((d?.teams ?? []).map((t) => t.id)), [d?.teams])
  const roster: PanelUser[] = useMemo(() => {
    const all = users.data ?? []
    if (privileged) return all
    if (role === 'project_manager') return all.filter((u) => u.team_id && scopedIds.has(u.team_id))
    return all
  }, [users.data, privileged, role, scopedIds])
  const shownTeams = (teams.data ?? []).filter((t) => privileged || scopedIds.has(t.id))
  const personRow = roster.find((u) => u.id === person)

  if (!canSeeTeam(role)) {
    return (
      <main id='content' className='flex flex-col gap-4 px-4 pt-6 pb-9 md:px-7 md:pt-7'>
        <PageHeader title='Team' subtitle='Not for your role' actions={<HeaderActions className='ms-0' />} />
        <p className='m-0 text-sm text-k-555555'>Team is for the owner, admins, supervisors and project managers.</p>
      </main>
    )
  }

  const tabs: { key: Tab; label: string; n?: number }[] = [
    { key: 'now', label: 'Now' },
    { key: 'people', label: 'People', n: roster.length || undefined },
    { key: 'teams', label: 'Teams & shifts', n: shownTeams.length || undefined },
  ]

  return (
    <main id='content' className='flex flex-col gap-[18px] px-4 pt-6 pb-9 md:px-7 md:pt-7'>
      <PageHeader
        title='Team'
        subtitle={d?.no_team ? 'No team yet' : scopeLine(role, myTeams, nowMs)}
        actions={
          <div className='flex items-center gap-2.5'>
            {privileged && (
              <button type='button' onClick={() => setInviting(true)} className='h-11 rounded-full bg-kb-202020 px-5 text-sm font-bold text-on-btn'>
                Invite people
              </button>
            )}
            <HeaderActions className='ms-0' />
          </div>
        }
      />

      {d?.no_team ? (
        <NoTeam />
      ) : (
        <>
          <div role='tablist' aria-label='Team' className='flex w-fit max-w-full flex-wrap gap-1 rounded-2xl bg-k-f2f2f2 p-1'>
            {tabs.map((t) => (
              <button
                key={t.key}
                type='button'
                role='tab'
                aria-selected={tab === t.key}
                onClick={() => setTab(t.key)}
                className={cn(
                  'flex h-10 items-center gap-2 rounded-xl px-4 text-[15px] focus-visible:outline-2 focus-visible:outline-k-6ea8ff',
                  tab === t.key ? 'bg-k-ffffff font-bold text-k-202020 shadow-sm' : 'text-k-444444'
                )}
              >
                {t.label}
                {t.n !== undefined && <span className='text-xs font-normal text-k-6b6b6b'>{t.n}</span>}
              </button>
            ))}
          </div>

          {tab === 'now' &&
            (now.isError ? (
              <div role='alert' className='flex flex-wrap items-center gap-3 rounded-2xl bg-k-fff3e0 px-4 py-3 text-sm text-k-8a4b00'>
                <span className='flex-1'>
                  <strong>Couldn’t load the floor.</strong> Nothing is shown rather than people shown as free who aren’t.
                </span>
                <button type='button' onClick={() => now.refetch()} className='h-9 rounded-full bg-kb-202020 px-4 text-[13px] font-bold text-on-btn'>
                  Try again
                </button>
              </div>
            ) : !d ? (
              <div className='grid grid-cols-1 gap-3.5 sm:grid-cols-2 xl:grid-cols-4' aria-busy='true'>
                {[0, 1, 2, 3].map((i) => (
                  <div key={i} className='h-32 animate-pulse rounded-[20px] bg-k-f7f7f7' />
                ))}
              </div>
            ) : (
              <Floor d={d} nowMs={nowMs} teamScoped={!privileged} onPerson={setPerson} />
            ))}

          {tab === 'people' && (
            <Roster
              users={roster}
              people={d?.people ?? []}
              teams={shownTeams}
              nowMs={nowMs}
              onPerson={setPerson}
              loading={users.isLoading}
              failed={users.isError}
            />
          )}

          {tab === 'teams' && (
            <TeamCards
              teams={shownTeams}
              cards={d?.teams ?? []}
              users={users.data ?? []}
              canManage={canManage}
              onSeePeople={() => setTab('people')}
            />
          )}
        </>
      )}

      <PersonDayPanel
        userId={person}
        onClose={() => setPerson(null)}
        teams={shownTeams}
        canMoveTeams={canManage}
        canManageAccess={privileged}
        chatEnabled={personRow ? personRow.chat_enabled !== false : undefined}
        nowMs={nowMs}
      />
      {privileged && <InviteDialog open={inviting} onOpenChange={setInviting} myRole={role} />}
    </main>
  )
}
