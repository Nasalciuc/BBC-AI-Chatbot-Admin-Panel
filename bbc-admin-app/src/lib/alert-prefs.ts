// Settings › Alerts, kept in this browser: the ring and the flashing tab are
// about this device's speakers and screen, so each computer and phone has
// its own. Pure apart from read/write — pinned by tests/alert-prefs.test.ts.

export type RingSound = 'classic' | 'soft' | 'two'

export interface AlertPrefs {
  /** Ring until the chat is opened. */
  ring: boolean
  /** The tab's name blinks "New chat". */
  flash: boolean
  sound: RingSound
  /** 0–100. */
  volume: number
  /** One short sound for each new chat in the queue. */
  beep: boolean
}

/** Today's behaviour before anyone touches Settings: everything on, full volume. */
export const DEFAULT_ALERT_PREFS: AlertPrefs = { ring: true, flash: true, sound: 'classic', volume: 100, beep: true }

export const ALERT_PREFS_KEY = 'bbc_alert_prefs'

const SOUNDS: RingSound[] = ['classic', 'soft', 'two']

/** Whatever is stored, a usable set: unknown or broken fields fall back. */
export function parseAlertPrefs(raw: string | null | undefined): AlertPrefs {
  let o: Record<string, unknown> = {}
  try {
    const v: unknown = raw ? JSON.parse(raw) : {}
    if (v && typeof v === 'object' && !Array.isArray(v)) o = v as Record<string, unknown>
  } catch {
    o = {}
  }
  const d = DEFAULT_ALERT_PREFS
  const vol = typeof o.volume === 'number' && Number.isFinite(o.volume) ? Math.round(o.volume) : d.volume
  return {
    ring: typeof o.ring === 'boolean' ? o.ring : d.ring,
    flash: typeof o.flash === 'boolean' ? o.flash : d.flash,
    sound: SOUNDS.includes(o.sound as RingSound) ? (o.sound as RingSound) : d.sound,
    volume: Math.min(100, Math.max(0, vol)),
    beep: typeof o.beep === 'boolean' ? o.beep : d.beep,
  }
}

export function readAlertPrefs(): AlertPrefs {
  try {
    return parseAlertPrefs(localStorage.getItem(ALERT_PREFS_KEY))
  } catch {
    // Storage blocked (private mode, policy): the defaults ring as before.
    return { ...DEFAULT_ALERT_PREFS }
  }
}

/** False when the browser refused to store it (the change holds until reload). */
export function writeAlertPrefs(p: AlertPrefs): boolean {
  try {
    localStorage.setItem(ALERT_PREFS_KEY, JSON.stringify(p))
    return true
  } catch {
    return false
  }
}

export const SOUND_LABEL: Record<RingSound, string> = { classic: 'Phone ring', soft: 'Soft chime', two: 'Two tones' }

/** The two sounds made in the browser: [frequency Hz, start s, length s].
 *  "Phone ring" is the recording (/notification.wav). */
export const SOUND_NOTES: Record<Exclude<RingSound, 'classic'>, [number, number, number][]> = {
  soft: [
    [659.25, 0, 0.6],
    [880, 0.2, 0.8],
  ],
  two: [
    [880, 0, 0.25],
    [660, 0.32, 0.25],
  ],
}

/** How often a made sound repeats while the chat waits. */
export const RING_REPEAT_MS = 1800

export function volumeLow(p: AlertPrefs): boolean {
  return p.ring && p.volume <= 30
}

/** "Michael Thompson" → "Michael T.", so a notification on a shared screen
 *  doesn't show the full name. A masked or missing name → the chat number. */
export function notifyName(name: string | null | undefined, chatNumber: number | null | undefined): string | null {
  const n = (name ?? '').trim()
  if (n && !n.includes('•')) {
    const parts = n.split(/\s+/)
    const first = parts[0]
    const last = parts.length > 1 ? parts[parts.length - 1] : ''
    return last ? `${first} ${last.charAt(0).toUpperCase()}.` : first
  }
  return chatNumber != null ? `#${chatNumber}` : null
}

/** What the browser notification says. A new chat has the first-reply
 *  deadline; a new message in a chat already yours does not. */
export function notificationText(
  kind: 'new' | 'message',
  name: string | null,
  seconds: number
): { title: string; body: string } {
  if (kind === 'message') {
    return { title: name ? `New message · ${name}` : 'New message', body: 'The client wrote in a chat you haven’t opened.' }
  }
  return { title: name ? `New chat for you · ${name}` : 'New chat for you', body: `Reply within ${seconds} seconds, or the AI answers.` }
}

export type NotifyPermission = 'granted' | 'default' | 'denied' | 'unsupported'

export const PERMISSION_TEXT: Record<NotifyPermission, { badge: string; text: string }> = {
  granted: { badge: 'On', text: 'On. A notification pops up even when the panel is behind other windows.' },
  default: { badge: 'Not asked', text: 'Not asked yet. Allow them so a new chat shows up even when the panel is behind other windows.' },
  denied: { badge: 'Blocked', text: 'Blocked in this browser, so you won’t see a notification when the panel is behind other windows.' },
  unsupported: { badge: 'None', text: 'This browser can’t show notifications. The ring and the tab still work.' },
}
