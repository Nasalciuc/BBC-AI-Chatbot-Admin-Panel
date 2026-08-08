/**
 * Truthful team display: the badge renders ONLY from a real users.team_id
 * relationship resolved against the teams list. NULL team_id → muted
 * "— no team", never a phantom badge.
 */
import { useQuery } from '@tanstack/react-query'
import { Badge } from '@/components/ui/badge'
import { getTeams } from '@/lib/api'
import { teamLabel } from './team-label'

export function TeamCell({ teamId }: { teamId: string | null | undefined }) {
  // Shares the ['teams'] cache with the Teams page and the header switcher.
  const { data: teams = [] } = useQuery({
    queryKey: ['teams'],
    queryFn: () => getTeams(),
  })

  const label = teamLabel(teamId, teams)
  if (!label) {
    return <span className='text-muted-foreground'>— no team</span>
  }
  return <Badge variant='outline'>{label}</Badge>
}
