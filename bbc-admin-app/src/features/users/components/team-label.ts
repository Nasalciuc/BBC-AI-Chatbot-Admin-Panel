/**
 * Truthful team resolution — pure, unit-testable.
 * A team label exists ONLY when users.team_id matches a real team row.
 */
export function teamLabel(
  teamId: string | null | undefined,
  teams: { id: string; name: string }[],
): string | null {
  if (!teamId) return null
  return teams.find((t) => t.id === teamId)?.name ?? null
}
