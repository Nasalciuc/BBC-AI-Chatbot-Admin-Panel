import { cn } from '@/lib/utils'
import { initials } from '@/features/chats/view'

export type AvatarTone = 'gray' | 'green' | 'blue' | 'amber' | 'purple' | 'orange' | 'yellow'

const TONES: Record<AvatarTone, string> = {
  gray: 'bg-k-f0f0f0 text-k-444444',
  green: 'bg-k-e3f6ea text-k-0a6b35',
  blue: 'bg-k-e6f0fb text-k-1f5fb0',
  amber: 'bg-k-fff1dc text-k-8a4b00',
  purple: 'bg-k-efeaff text-k-4a36c4',
  orange: 'bg-k-fde3d6 text-k-7a2e0e',
  yellow: 'bg-k-fdf1d0 text-k-7a5300',
}

const ROTATION: AvatarTone[] = ['green', 'yellow', 'purple', 'blue', 'orange', 'gray']

/** A stable tint for a name, so the same person keeps their colour across pages. */
function toneFor(name: string | null | undefined): AvatarTone {
  const s = name ?? ''
  let h = 0
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0
  return ROTATION[h % ROTATION.length]
}

/** A person's initials in a tinted circle (agents, users — not clients). */
export function PersonAvatar({
  name,
  tone,
  size = 32,
  className,
}: {
  name: string | null | undefined
  tone?: AvatarTone
  size?: 30 | 32 | 36 | 52
  className?: string
}) {
  const box =
    size === 52 ? 'size-[52px] text-lg' : size === 36 ? 'size-9 text-[13px]' : size === 32 ? 'size-8 text-xs' : 'size-[30px] text-[11px]'
  return (
    <span aria-hidden='true' className={cn('flex flex-none items-center justify-center rounded-full font-bold', box, TONES[tone ?? toneFor(name)], className)}>
      {initials(name)}
    </span>
  )
}
