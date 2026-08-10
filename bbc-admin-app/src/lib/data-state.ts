/**
 * Tri-state data resolution: loading / error / ready — pure, unit-testable.
 * An error state must NEVER be rendered with success copy; the resolver
 * makes the three states structurally distinct so no caller can conflate
 * "failed to load" with "genuinely zero".
 */

export type DataStateKind = 'loading' | 'error' | 'ready'

/** Error always wins over loading — a failed refetch while polling must
 *  surface, not hide behind a spinner. */
export function resolveDataState(q: {
  isLoading: boolean
  isError: boolean
}): DataStateKind {
  if (q.isError) return 'error'
  if (q.isLoading) return 'loading'
  return 'ready'
}
