import { skipToken, useQuery } from '@tanstack/react-query'

/**
 * The follow-up lottery's badge: leads given to me that I haven't opened, as the last heartbeat said it.
 * Server data, so it lives in the query cache with the rest (a sign-out's queryClient.clear() resets it), not in
 * a Zustand store (CodeRabbit #273). use-heartbeat.ts writes it on every beat; nothing fetches it on its own.
 * A number (0 is a confirmed "none"), or null: the server says nothing (the feature is off for me, or Redis
 * can't say), or no beat has answered yet.
 */
export const FOLLOWUPS_NEW_KEY = ['followups-new'] as const

export function useFollowupsNew(): number | null {
  const { data } = useQuery<number | null>({ queryKey: FOLLOWUPS_NEW_KEY, queryFn: skipToken, staleTime: Infinity })
  return data ?? null
}
