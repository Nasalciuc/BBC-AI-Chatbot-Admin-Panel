import { type ReactNode, useState } from 'react'
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

/**
 * The person's photo, filling the circle it sits in — or `fallback` (their
 * initials) when they have none or it doesn't load. A changed link is tried again.
 */
export function AvatarPhoto({ url, fallback }: { url: string | null | undefined; fallback: ReactNode }) {
  const [failed, setFailed] = useState<string | null>(null)
  const src = url?.trim()
  if (!src || failed === src) return <>{fallback}</>
  return (
    <img
      src={src}
      alt=''
      loading='lazy'
      decoding='async'
      draggable={false}
      referrerPolicy='no-referrer'
      onError={() => setFailed(src)}
      className='size-full rounded-full object-cover'
    />
  )
}

/** A person's photo — or their initials in a tinted circle (agents, users — not clients). */
export function PersonAvatar({
  name,
  url,
  tone,
  size = 32,
  className,
}: {
  name: string | null | undefined
  /** Their photo (users.avatar_url); null = initials. */
  url: string | null | undefined
  tone?: AvatarTone
  size?: 30 | 32 | 36 | 52
  className?: string
}) {
  const box =
    size === 52 ? 'size-[52px] text-lg' : size === 36 ? 'size-9 text-[13px]' : size === 32 ? 'size-8 text-xs' : 'size-[30px] text-[11px]'
  return (
    <span aria-hidden='true' className={cn('flex flex-none items-center justify-center rounded-full font-bold', box, TONES[tone ?? toneFor(name)], className)}>
      <AvatarPhoto url={url} fallback={initials(name)} />
    </span>
  )
}
