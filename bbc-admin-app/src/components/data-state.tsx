/**
 * Tri-state data rendering: loading / error / ready.
 *
 * The dashboard was rendering a failed or empty stats fetch as GOOD NEWS
 * ("All contacted ✓", "All leads contacted! 🎉") — an error state must
 * NEVER wear success copy. This component owns the loading and error
 * variants; "ready" (including genuinely-zero data) stays with the caller.
 * The pure resolver lives in @/lib/data-state (unit-tested without a DOM).
 */

import { AlertTriangle } from 'lucide-react'
import type { DataStateKind } from '@/lib/data-state'

export function DataStateView({
  state,
  what,
  children,
}: {
  state: DataStateKind
  /** Short noun for the messages, e.g. "dashboard stats", "conversations". */
  what: string
  children?: React.ReactNode
}) {
  if (state === 'loading') {
    return (
      <div className='flex items-center justify-center py-16 text-sm text-muted-foreground'>
        Loading {what}...
      </div>
    )
  }
  if (state === 'error') {
    return (
      <div className='flex items-center justify-center gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-6 text-sm font-medium text-red-700'>
        <AlertTriangle className='h-4 w-4 shrink-0' />
        Couldn&apos;t load {what} — retrying automatically.
      </div>
    )
  }
  return <>{children}</>
}
