import type { ComponentChildren } from 'preact'
import { useState } from 'preact/hooks'
import brand from './config'
import { ALEX_PHOTO } from './alexPhoto'

interface Props {
  /** The spot's own classes: the header, beside a message, or beside the corner button. */
  cls: string
  /** The "AI" badge on the photo: the panel's header only (owner, 16 Nov: one place is enough). */
  badge?: boolean
  children?: ComponentChildren
}

/**
 * Alex's round avatar, the same in the three spots that show him: his photo, part of the bundle so no
 * request can lose it. A page whose rules refuse embedded images (a Content-Security-Policy without
 * `data:`) fires the image's error, and the letter on the brand colour shows instead, as before.
 *
 * With `badge` (the header), the "AI" badge sits on it, on the photo and on the letter alike: the line under
 * his name says "Travel Agent", and the badge says what kind, at the top of the chat for all of it. Decorative
 * (aria-hidden), as the photo is: screen readers hear the same word in the line itself (PersonaLine).
 */
export function PersonaAvatar({ cls, badge = false, children }: Props) {
  const [refused, setRefused] = useState(false)
  return (
    <span class={refused ? cls : `${cls} bw-photo`} aria-hidden="true">
      {refused
        ? brand.persona.initial
        : <img src={ALEX_PHOTO} alt="" draggable={false} onError={() => setRefused(true)} />}
      {badge && <i class="bw-ai">AI</i>}
      {children}
    </span>
  )
}
