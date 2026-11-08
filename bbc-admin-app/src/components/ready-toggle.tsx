import { cn } from '@/lib/utils'
import { useReadyToggle } from '@/hooks/use-ready-toggle'

/** The "Ready for chats" pill. Renders nothing for roles that don't take chats. */
export function ReadyToggle({ compact = false }: { compact?: boolean }) {
  const { canBeReady, ready, toggle } = useReadyToggle()
  if (!canBeReady) return null

  return (
    <button
      type='button'
      aria-pressed={ready}
      onClick={toggle}
      className={cn(
        'flex h-10 flex-none items-center gap-2 rounded-full border text-sm font-bold transition-colors focus-visible:outline-2 focus-visible:outline-k-6ea8ff',
        compact ? 'px-3.5' : 'px-4',
        ready
          ? 'border-transparent bg-k-00c450/12 text-k-0a6b35'
          : 'border-k-c9c9c9 bg-k-ffffff text-k-444444'
      )}
    >
      <span aria-hidden='true' className={cn('size-2 rounded-full', ready ? 'bg-k-00a645' : 'bg-k-9c9c9c')} />
      {compact ? (ready ? 'Ready' : 'Not ready') : ready ? 'Ready for chats' : 'Not ready'}
    </button>
  )
}
