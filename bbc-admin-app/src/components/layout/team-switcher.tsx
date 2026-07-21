import { ChevronsUpDown, Plus, Check, UsersRound } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { getTeams } from '@/lib/api'
import { usePermissions } from '@/lib/bbc/hooks'
import type { Team, UserRole } from '@/lib/bbc/types'
import { useAuthStore } from '@/stores/auth-store'
import { useTeamStore } from '@/stores/team-store'
import { Logo } from '@/assets/logo'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from '@/components/ui/sidebar'

function formatShift(team: Team): string | null {
  const hhmm = (v?: string | null) => (v ? v.slice(0, 5) : null)
  const start = hhmm(team.shift_start)
  const end = hhmm(team.shift_end)
  if (team.shift_name && start && end) return `${team.shift_name} · ${start}–${end}`
  if (start && end) return `${start}–${end}`
  return team.shift_name || null
}

/**
 * Header team switcher. Loads the teams the current user is allowed to see
 * (server-scoped) and sets an active-team context. Rendered by AppSidebar only
 * when `canViewTeams` is true, so sales/support never mount it.
 */
export function TeamSwitcher() {
  const { isMobile } = useSidebar()
  const navigate = useNavigate()
  const role = (useAuthStore((s) => s.auth.user?.role) ?? 'sales') as UserRole
  const permissions = usePermissions(role)
  const { activeTeamId, setActiveTeam } = useTeamStore()

  // Shares the ['teams'] cache with the Teams management page (no duplicate fetch).
  const { data: teams = [], isLoading } = useQuery({
    queryKey: ['teams'],
    queryFn: () => getTeams(),
  })

  const activeTeam = activeTeamId
    ? (teams.find((t) => t.id === activeTeamId) ?? null)
    : null

  // While loading, or when the user has no teams (e.g. a supervisor not yet
  // assigned), show the static brand entry — never an empty/flickering dropdown.
  if (isLoading || teams.length === 0) {
    return (
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton size='lg'>
            <div className='flex aspect-square size-8 items-center justify-center rounded-lg'>
              <Logo className='size-6' />
            </div>
            <div className='grid flex-1 text-start text-sm leading-tight'>
              <span className='truncate font-semibold'>BuyBusinessClass</span>
              <span className='truncate text-xs'>Admin Panel</span>
            </div>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    )
  }

  const triggerTitle = activeTeam ? activeTeam.name : 'All teams'
  const triggerSubtitle = activeTeam
    ? (formatShift(activeTeam) ?? 'Team')
    : 'BuyBusinessClass'

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <SidebarMenuButton
              size='lg'
              className='data-[state=open]:bg-sidebar-accent data-[state=open]:text-sidebar-accent-foreground'
            >
              <div className='flex aspect-square size-8 items-center justify-center rounded-lg'>
                {activeTeam ? <UsersRound className='size-6' /> : <Logo className='size-6' />}
              </div>
              <div className='grid flex-1 text-start text-sm leading-tight'>
                <span className='truncate font-semibold'>{triggerTitle}</span>
                <span className='truncate text-xs'>{triggerSubtitle}</span>
              </div>
              <ChevronsUpDown className='ms-auto' />
            </SidebarMenuButton>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            className='w-(--radix-dropdown-menu-trigger-width) min-w-56 rounded-lg'
            align='start'
            side={isMobile ? 'bottom' : 'right'}
            sideOffset={4}
          >
            <DropdownMenuLabel className='text-xs text-muted-foreground'>
              Teams
            </DropdownMenuLabel>

            <DropdownMenuItem
              onClick={() => setActiveTeam(null)}
              className='gap-2 p-2'
            >
              <div className='flex size-6 items-center justify-center rounded-sm border'>
                <Logo className='size-4 shrink-0' />
              </div>
              <span className='flex-1'>All teams</span>
              {activeTeamId === null && <Check className='size-4' />}
            </DropdownMenuItem>

            <DropdownMenuSeparator />

            {teams.map((team) => {
              const shift = formatShift(team)
              return (
                <DropdownMenuItem
                  key={team.id}
                  onClick={() => setActiveTeam(team.id)}
                  className='gap-2 p-2'
                >
                  <div className='flex size-6 items-center justify-center rounded-sm border'>
                    <UsersRound className='size-4 shrink-0' />
                  </div>
                  <div className='grid flex-1 leading-tight'>
                    <span className='truncate'>{team.name}</span>
                    {shift && (
                      <span className='truncate text-xs text-muted-foreground'>
                        {shift}
                      </span>
                    )}
                  </div>
                  {activeTeamId === team.id && <Check className='size-4' />}
                </DropdownMenuItem>
              )
            })}

            {permissions.canManageTeams && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  className='gap-2 p-2'
                  onClick={() => navigate({ to: '/teams' })}
                >
                  <div className='flex size-6 items-center justify-center rounded-md border bg-background'>
                    <Plus className='size-4' />
                  </div>
                  <div className='font-medium text-muted-foreground'>Add team</div>
                </DropdownMenuItem>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
