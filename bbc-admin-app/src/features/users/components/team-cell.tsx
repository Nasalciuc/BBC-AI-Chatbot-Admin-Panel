/**
 * Truthful team display: the name renders ONLY from a real users.team_id
 * relationship resolved against the teams list. NULL team_id → muted
 * "— no team", never a phantom badge.
 */
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { getTeams } from '@/lib/api'
import { teamLabel } from './team-label'

export function TeamCell({ teamId }: { teamId: string | null | undefined }) {
  // Shares the ['teams'] cache with the Teams page.
  const { data: teams = [] } = useQuery({
    queryKey: ['teams'],
    queryFn: () => getTeams(),
  })

  const label = teamLabel(teamId, teams)
  if (!label) {
    return <span className='text-k-6b6b6b'>— no team</span>
  }
  return (
    <span>
      {label}{' '}
      <Link to='/teams' className='text-k-5b45e0 underline-offset-2 hover:underline'>
        See in Team
      </Link>
    </span>
  )
}
