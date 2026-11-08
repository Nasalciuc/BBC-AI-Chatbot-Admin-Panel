import { cn } from '@/lib/utils'

/** Initials in a tinted circle, with the client's presence dot. */
export function ChatAvatar({
  text,
  tone,
  dot,
  ring = 'border-k-ffffff',
  size = 36,
  className,
}: {
  text: string
  /** bg + text classes (see view.ts avatarClasses) */
  tone: string
  /** bg class for the presence dot, or null for none */
  dot?: string | null
  /** border class matching what the avatar sits on */
  ring?: string
  size?: 32 | 36 | 42 | 44
  className?: string
}) {
  const box = size === 32 ? 'size-8 text-xs' : size === 36 ? 'size-9 text-[13px]' : size === 42 ? 'size-[42px] text-[15px]' : 'size-11 text-sm'
  const d = size >= 42 ? 'size-3' : 'size-2.5'
  return (
    <span aria-hidden='true' className={cn('relative flex flex-none items-center justify-center rounded-full font-bold', box, tone, className)}>
      {text}
      {dot && <span className={cn('absolute -end-px -bottom-px rounded-full border-2', d, dot, ring)} />}
    </span>
  )
}
