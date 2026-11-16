import type { ComponentChildren } from 'preact'
import { useState } from 'preact/hooks'
import brand from './config'
import { ALEX_PHOTO } from './alexPhoto'

interface Props {
  /** The spot's own classes: the header, beside a message, or beside the corner button. */
  cls: string
  children?: ComponentChildren
}

/**
 * Alex's round avatar, the same in the three spots that show him: his photo, part of the bundle so no
 * request can lose it. A page whose rules refuse embedded images (a Content-Security-Policy without
 * `data:`) fires the image's error, and the letter on the brand colour shows instead, as before.
 *
 * The "AI" badge sits on it in every spot, on the photo and on the letter alike: the line under his name
 * says "Travel Agent" (owner, 16 Nov), and the badge says what kind, where the eye already is. Decorative
 * (aria-hidden), as the photo is: screen readers hear the same word in the line itself (PersonaLine).
 */
export function PersonaAvatar({ cls, children }: Props) {
  const [refused, setRefused] = useState(false)
  return (
    <span class={refused ? cls : `${cls} bw-photo`} aria-hidden="true">
      {refused
        ? brand.persona.initial
        : <img src={ALEX_PHOTO} alt="" draggable={false} onError={() => setRefused(true)} />}
      <i class="bw-ai">AI</i>
      {children}
    </span>
  )
}
