import type { ComponentChildren } from 'preact'
// Icons drawn from lucide (ISC licence), inlined so the widget loads nothing extra.
// One stroke weight; colour comes from the parent's `color`.

type IconProps = { size?: number }

function Svg({ size = 20, children }: IconProps & { children: ComponentChildren }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      {children}
    </svg>
  )
}

export const IconClose = (p: IconProps) => (
  <Svg {...p}><path d="M18 6 6 18" /><path d="m6 6 12 12" /></Svg>
)

export const IconPhone = (p: IconProps) => (
  <Svg {...p}>
    <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z" />
  </Svg>
)

export const IconArrowUp = (p: IconProps) => (
  <Svg {...p}><path d="m5 12 7-7 7 7" /><path d="M12 19V5" /></Svg>
)

export const IconArrowRight = (p: IconProps) => (
  <Svg {...p}><path d="M5 12h14" /><path d="m12 5 7 7-7 7" /></Svg>
)

export const IconAlert = (p: IconProps) => (
  <Svg {...p}><circle cx="12" cy="12" r="10" /><path d="M12 8v4" /><path d="M12 16h.01" /></Svg>
)

export const IconArrowDown = (p: IconProps) => (
  <Svg {...p}><path d="M12 5v14" /><path d="m19 12-7 7-7-7" /></Svg>
)

// lucide maximize-2 / minimize-2: the chat over the whole screen, and back.
export const IconExpand = (p: IconProps) => (
  <Svg {...p}><path d="M15 3h6v6" /><path d="m21 3-7 7" /><path d="m3 21 7-7" /><path d="M9 21H3v-6" /></Svg>
)

export const IconShrink = (p: IconProps) => (
  <Svg {...p}><path d="m14 10 7-7" /><path d="M20 10h-6V4" /><path d="m3 21 7-7" /><path d="M4 14h6v6" /></Svg>
)

// lucide message-circle and mail: where the agent writes (channel.ts) — WhatsApp and email.
export const IconMessage = (p: IconProps) => (
  <Svg {...p}><path d="M7.9 20A9 9 0 1 0 4 16.1L2 22Z" /></Svg>
)

export const IconMail = (p: IconProps) => (
  <Svg {...p}><rect width="20" height="16" x="2" y="4" rx="2" /><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" /></Svg>
)
