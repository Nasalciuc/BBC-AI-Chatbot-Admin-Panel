import { useState } from 'react'
import { Link } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import useDialogState from '@/hooks/use-dialog-state'
import { useAuthStore } from '@/stores/auth-store'
import { usePermissions } from '@/lib/bbc/hooks'
import type { UserRole } from '@/lib/bbc/types'
import { getUsers } from '@/lib/api'
import type { TeamUser } from '@/features/teams/data/types'
import { TeamDialog } from '@/features/teams/components/team-dialog'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuShortcut,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { SignOutDialog } from '@/components/sign-out-dialog'

export function ProfileDropdown() {
  const [open, setOpen] = useDialogState()
  const [createTeamOpen, setCreateTeamOpen] = useState(false)
  const { auth } = useAuthStore()
  const role = (auth.user?.role ?? 'sales') as UserRole
  const permissions = usePermissions(role)
  const userName = auth.user?.name || auth.user?.email || 'User'
  const userEmail = auth.user?.email || ''
  const initials = userName
    .split(' ')
    .map((n) => n[0])
    .join('')
    .toUpperCase()
    .slice(0, 2)

  // Supervisor / PM option lists for the create dialog (only for team managers).
  const { data: teamUsers = [] } = useQuery({
    queryKey: ['teams-users'],
    queryFn: async () => {
      const res = await getUsers()
      if (!res.success) throw new Error('Failed to load users')
      return (res.data ?? []) as unknown as TeamUser[]
    },
    enabled: permissions.canManageTeams,
  })
  const supervisors = teamUsers.filter((u) => u.role === 'supervisor')
  const projectManagers = teamUsers.filter((u) => u.role === 'project_manager')

  return (
    <>
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <Button variant='ghost' className='relative h-8 w-8 rounded-full'>
            <Avatar className='h-8 w-8'>
              <AvatarImage src={auth.user?.avatar_url || ''} alt={userName} />
              <AvatarFallback>{initials}</AvatarFallback>
            </Avatar>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent className='w-56' align='end' forceMount>
          <DropdownMenuLabel className='font-normal'>
            <div className='flex flex-col gap-1.5'>
              <p className='text-sm leading-none font-medium'>{userName}</p>
              <p className='text-xs leading-none text-muted-foreground'>
                {userEmail}
              </p>
            </div>
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuGroup>
            <DropdownMenuItem asChild>
              <Link to='/settings'>
                Profile
                <DropdownMenuShortcut>⇧⌘P</DropdownMenuShortcut>
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link to='/settings'>
                Billing
                <DropdownMenuShortcut>⌘B</DropdownMenuShortcut>
              </Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link to='/settings'>
                Settings
                <DropdownMenuShortcut>⌘S</DropdownMenuShortcut>
              </Link>
            </DropdownMenuItem>
            {permissions.canManageTeams && (
              <DropdownMenuItem onSelect={() => setCreateTeamOpen(true)}>
                New Team
              </DropdownMenuItem>
            )}
          </DropdownMenuGroup>
          <DropdownMenuSeparator />
          <DropdownMenuItem variant='destructive' onClick={() => setOpen(true)}>
            Sign out
            <DropdownMenuShortcut className='text-current'>
              ⇧⌘Q
            </DropdownMenuShortcut>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <SignOutDialog open={!!open} onOpenChange={setOpen} />

      {permissions.canManageTeams && (
        <TeamDialog
          open={createTeamOpen}
          onOpenChange={setCreateTeamOpen}
          supervisors={supervisors}
          projectManagers={projectManagers}
        />
      )}
    </>
  )
}
