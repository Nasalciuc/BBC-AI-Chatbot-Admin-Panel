import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { MoreHorizontal, Plus, UsersRound } from 'lucide-react'
import { toast } from 'sonner'
import type { Team } from '@/lib/bbc/types'
import type { UserRole } from '@/lib/bbc/types'
import { usePermissions } from '@/lib/bbc/hooks'
import { getTeams, getUsers } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'
import { Header } from '@/components/layout/header'
import { HeaderActions } from '@/components/header-actions'
import { Main } from '@/components/layout/main'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { type TeamUser, fmtShift } from './data/types'
import { TeamDialog } from './components/team-dialog'
import { TeamMembersDialog } from './components/team-members-dialog'
import { TeamDeactivateDialog } from './components/team-deactivate-dialog'

export function Teams() {
  const role = (useAuthStore((s) => s.auth.user?.role) ?? 'sales') as UserRole
  const permissions = usePermissions(role)

  const {
    data: teams = [],
    isLoading: teamsLoading,
    isError: teamsError,
  } = useQuery({
    queryKey: ['teams'],
    queryFn: () => getTeams(),
    enabled: permissions.canManageTeams,
    meta: { errorToast: false },
  })

  const { data: users = [] } = useQuery({
    queryKey: ['teams-users'],
    queryFn: async () => {
      const res = await getUsers()
      if (!res.success) throw new Error('Failed to load users')
      return (res.data ?? []) as unknown as TeamUser[]
    },
    enabled: permissions.canManageTeams,
    meta: { errorToast: false },
  })

  const [createOpen, setCreateOpen] = useState(false)
  const [editTeam, setEditTeam] = useState<Team | null>(null)
  const [membersTeam, setMembersTeam] = useState<Team | null>(null)
  const [deactivateTeam, setDeactivateTeam] = useState<Team | null>(null)

  const usersById = useMemo(() => {
    const map = new Map<string, TeamUser>()
    for (const u of users) map.set(u.id, u)
    return map
  }, [users])

  const memberCounts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const u of users) {
      if (u.team_id) counts.set(u.team_id, (counts.get(u.team_id) ?? 0) + 1)
    }
    return counts
  }, [users])

  const supervisors = useMemo(() => users.filter((u) => u.role === 'supervisor'), [users])
  const projectManagers = useMemo(() => users.filter((u) => u.role === 'project_manager'), [users])

  const nameOf = (id?: string | null) => {
    if (!id) return '—'
    const u = usersById.get(id)
    return u?.name || u?.email || id
  }

  if (!permissions.canManageTeams) {
    return (
      <>
        <Header fixed>
          <HeaderActions />
        </Header>
        <Main>
          <div className='flex h-[60vh] flex-col items-center justify-center text-center'>
            <UsersRound className='mb-3 h-10 w-10 text-muted-foreground' />
            <h2 className='text-xl font-semibold'>Not authorized</h2>
            <p className='text-muted-foreground'>
              You don&apos;t have permission to manage teams.
            </p>
          </div>
        </Main>
      </>
    )
  }

  return (
    <>
      <Header fixed>
        <HeaderActions />
      </Header>

      <Main className='flex flex-1 flex-col gap-4 sm:gap-6'>
        <div className='flex flex-wrap items-end justify-between gap-2'>
          <div>
            <h2 className='text-2xl font-bold tracking-tight'>Teams</h2>
            <p className='text-muted-foreground'>
              Create teams, set shifts, and assign supervisors, PMs, and operators.
            </p>
          </div>
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className='me-2 h-4 w-4' /> Create team
          </Button>
        </div>

        <div className='rounded-lg border'>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Shift</TableHead>
                <TableHead>Supervisor</TableHead>
                <TableHead>PM</TableHead>
                <TableHead className='text-center'>Members</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className='w-[60px]' />
              </TableRow>
            </TableHeader>
            <TableBody>
              {teamsLoading && (
                <TableRow>
                  <TableCell colSpan={7} className='h-24 text-center text-muted-foreground'>
                    Loading teams…
                  </TableCell>
                </TableRow>
              )}
              {teamsError && !teamsLoading && (
                <TableRow>
                  <TableCell colSpan={7} className='h-24 text-center text-destructive'>
                    Failed to load teams — check your connection.
                  </TableCell>
                </TableRow>
              )}
              {!teamsLoading && !teamsError && teams.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} className='h-24 text-center text-muted-foreground'>
                    No teams yet. Click “Create team” to add one.
                  </TableCell>
                </TableRow>
              )}
              {teams.map((team) => (
                <TableRow key={team.id}>
                  <TableCell className='font-medium'>{team.name}</TableCell>
                  <TableCell>{fmtShift(team.shift_name, team.shift_start, team.shift_end)}</TableCell>
                  <TableCell>{nameOf(team.supervisor_id)}</TableCell>
                  <TableCell>{nameOf(team.pm_id)}</TableCell>
                  <TableCell className='text-center'>{memberCounts.get(team.id) ?? 0}</TableCell>
                  <TableCell>
                    <Badge variant={team.is_active ? 'default' : 'secondary'}>
                      {team.is_active ? 'Active' : 'Inactive'}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant='ghost' size='icon' className='h-8 w-8'>
                          <MoreHorizontal className='h-4 w-4' />
                          <span className='sr-only'>Open actions</span>
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align='end'>
                        <DropdownMenuItem onClick={() => setEditTeam(team)}>Edit</DropdownMenuItem>
                        <DropdownMenuItem onClick={() => setMembersTeam(team)}>
                          Manage members
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          className='text-destructive'
                          disabled={!team.is_active}
                          onClick={() => setDeactivateTeam(team)}
                        >
                          Deactivate
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      </Main>

      <TeamDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        supervisors={supervisors}
        projectManagers={projectManagers}
      />
      {editTeam && (
        <TeamDialog
          open={!!editTeam}
          onOpenChange={(o) => !o && setEditTeam(null)}
          team={editTeam}
          supervisors={supervisors}
          projectManagers={projectManagers}
        />
      )}
      {membersTeam && (
        <TeamMembersDialog
          open={!!membersTeam}
          onOpenChange={(o) => !o && setMembersTeam(null)}
          team={membersTeam}
          users={users}
        />
      )}
      {deactivateTeam && (
        <TeamDeactivateDialog
          open={!!deactivateTeam}
          onOpenChange={(o) => !o && setDeactivateTeam(null)}
          team={deactivateTeam}
          onDone={() => {
            toast.success('Team deactivated')
            setDeactivateTeam(null)
          }}
        />
      )}
    </>
  )
}
