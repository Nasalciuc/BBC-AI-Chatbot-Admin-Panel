/**
 * Persistent assignment alerts — rings like a phone until the operator opens
 * the conversation or the first-reply deadline hands it back to the AI.
 *
 * How it rings (sound, volume, flashing tab, the queue chime) is Settings ›
 * Alerts, kept in this browser (alert-prefs.ts). Untouched settings ring
 * exactly as before: the recording, full volume, flashing tab.
 */
// Relative import with extension on purpose — the pure rules are node-tested.
import {
  RING_REPEAT_MS,
  SOUND_NOTES,
  notificationText,
  readAlertPrefs,
  type NotifyPermission,
  type RingSound,
} from './alert-prefs.ts'
// Every browser notification carries the company's logo — the image the
// example in Settings › Alerts draws, so the example is the real thing.
import { LOGO_SRC } from './brand.ts'

let _ringAudio: HTMLAudioElement | null = null
/** The made sounds (Soft chime, Two tones) repeat on this timer. */
let _ringTimer: ReturnType<typeof setInterval> | null = null
let _flashInterval: ReturnType<typeof setInterval> | null = null
let _alertsActive = false
let _audioUnlocked = false
let _beepCtx: AudioContext | null = null
/** Ends a Settings test alert early when a real chat arrives. */
let _stopTest: (() => void) | null = null
const _baseTitle = 'BBC Admin Panel'
const _flashTitle = '🔔 New chat — BBC Admin Panel'

function _audioCtx(): AudioContext | null {
  try {
    if (!_beepCtx) _beepCtx = new AudioContext()
    if (_beepCtx.state === 'suspended') void _beepCtx.resume()
    return _beepCtx
  } catch {
    return null // AudioContext not available
  }
}

function _playFallbackBeep(volume = 1): void {
  const ctx = _audioCtx()
  if (!ctx) return
  try {
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.type = 'sine'
    osc.frequency.value = 800
    gain.gain.value = 0.3 * volume
    osc.connect(gain).connect(ctx.destination)
    osc.start()
    osc.stop(ctx.currentTime + 0.2)
  } catch {
    // AudioContext not available
  }
}

/** One go of a made sound (soft rise, or high-low), at `volume` 0–1. */
function _playNotes(sound: Exclude<RingSound, 'classic'>, volume: number): void {
  const ctx = _audioCtx()
  if (!ctx || volume <= 0) return
  try {
    const t0 = ctx.currentTime + 0.02
    for (const [freq, start, len] of SOUND_NOTES[sound]) {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.type = sound === 'soft' ? 'sine' : 'triangle'
      osc.frequency.value = freq
      gain.gain.setValueAtTime(0.0001, t0 + start)
      gain.gain.exponentialRampToValueAtTime(0.4 * volume, t0 + start + 0.02)
      gain.gain.exponentialRampToValueAtTime(0.0001, t0 + start + len)
      osc.connect(gain).connect(ctx.destination)
      osc.start(t0 + start)
      osc.stop(t0 + start + len + 0.05)
    }
  } catch {
    // AudioContext not available
  }
}

/**
 * Start (or keep) the alert. Safe to call repeatedly — the caller re-asserts
 * "work is still unattended" on every poll, so an already-playing ring is left
 * alone instead of being restarted from the top.
 *
 * `kind`: a new chat has the first-reply deadline (`seconds`); a new message
 * in a chat already yours does not. `name` is already short ("Michael T.").
 */
export function notifyAssignment(info?: { name?: string | null; kind?: 'new' | 'message'; seconds?: number }): void {
  const alreadyAlerting = _alertsActive
  _alertsActive = true
  // A real chat outranks a Settings test.
  _stopTest?.()
  const prefs = readAlertPrefs()

  // Settings changed while it rings: stop what is no longer wanted.
  const audible = prefs.ring && prefs.volume > 0
  if ((!audible || prefs.sound !== 'classic') && _ringAudio && !_ringAudio.paused) {
    _ringAudio.pause()
    _ringAudio.currentTime = 0
  }
  if ((!audible || prefs.sound === 'classic') && _ringTimer) {
    clearInterval(_ringTimer)
    _ringTimer = null
  }
  if (!prefs.flash && _flashInterval) {
    clearInterval(_flashInterval)
    _flashInterval = null
    document.title = _baseTitle
  }

  // 1. Sound LOOP — one ring at a time, never stacked
  if (audible) {
    if (prefs.sound === 'classic') {
      try {
        if (!_ringAudio) {
          _ringAudio = new Audio('/notification.wav')
          _ringAudio.loop = true
        }
        // The autoplay-unlock path plays muted; make sure a real ring is audible.
        _ringAudio.volume = prefs.volume / 100
        if (_ringAudio.paused) {
          _ringAudio.currentTime = 0
          _ringAudio.play().catch(() => {
            // Fallback: programmatic beep if wav fails (404, autoplay, codec)
            _playFallbackBeep(prefs.volume / 100)
          })
        }
      } catch {
        /* no audio */
      }
    } else if (!_ringTimer) {
      const sound = prefs.sound
      const volume = prefs.volume / 100
      _playNotes(sound, volume)
      _ringTimer = setInterval(() => _playNotes(sound, volume), RING_REPEAT_MS)
    }
  }

  // 2. Title FLASH every 800ms
  if (prefs.flash && !_flashInterval) {
    _flashInterval = setInterval(() => {
      document.title = document.title === _baseTitle ? _flashTitle : _baseTitle
    }, 800)
  }

  // 3. Browser notification — once per alert, not once per poll
  if (!alreadyAlerting && 'Notification' in window && Notification.permission === 'granted') {
    try {
      const t = notificationText(info?.kind ?? 'new', info?.name ?? null, info?.seconds ?? 90)
      const n = new Notification(t.title, {
        body: t.body,
        icon: LOGO_SRC,
        tag: 'bbc-assign',
        requireInteraction: true,
      })
      n.onclick = () => {
        window.focus()
        n.close()
      }
    } catch {
      /* */
    }
  }
}

/**
 * One-shot chime for the shared queue — a conversation ARRIVED in the line.
 * Deliberately not notifyAssignment(): that one LOOPS until attended, which is
 * right for "this is yours and waiting" and wrong for "somebody could take
 * this". One sound per conversation, no flashing title, no notification.
 */
export function playQueueChime(): void {
  const prefs = readAlertPrefs()
  if (!prefs.beep || prefs.volume <= 0) return
  try {
    const a = new Audio('/notification.wav')
    a.volume = 0.6 * (prefs.volume / 100)
    a.play().catch(() => {
      /* autoplay blocked — the badge still shows the queue */
    })
  } catch {
    /* no audio */
  }
}

/**
 * A task reminder (Tasks): one sound and one browser notification, the way
 * the person set alerts in Settings → Alerts. Never loops and never flashes
 * the tab: a reminder isn't a client waiting.
 */
export function notifyTaskReminder(title: string, body: string, tag: string): void {
  const prefs = readAlertPrefs()
  if ((prefs.ring || prefs.beep) && prefs.volume > 0) {
    try {
      if (prefs.sound === 'classic') {
        const a = new Audio('/notification.wav')
        a.volume = 0.7 * (prefs.volume / 100)
        a.play().catch(() => _playFallbackBeep(prefs.volume / 100))
      } else {
        _playNotes(prefs.sound, prefs.volume / 100)
      }
    } catch {
      /* no audio: the notification and the menu count still show it */
    }
  }
  if ('Notification' in window && Notification.permission === 'granted') {
    try {
      const n = new Notification(title, { body, icon: LOGO_SRC, tag: `bbc-task-${tag}` })
      n.onclick = () => {
        window.focus()
        n.close()
      }
    } catch {
      /* notifications blocked in this context */
    }
  }
}

export function stopAssignmentAlerts(): void {
  _alertsActive = false
  if (_ringAudio) {
    _ringAudio.pause()
    _ringAudio.currentTime = 0
  }
  if (_ringTimer) {
    clearInterval(_ringTimer)
    _ringTimer = null
  }
  if (_flashInterval) {
    clearInterval(_flashInterval)
    _flashInterval = null
  }
  document.title = _baseTitle
}

// ── Settings › Alerts ────────────────────────────────────────────────────

/** "Play": the chosen sound once, at the chosen volume (0–100). */
export function previewRing(sound: RingSound, volume: number): void {
  if (volume <= 0) return
  if (sound !== 'classic') {
    _playNotes(sound, volume / 100)
    return
  }
  try {
    const a = new Audio('/notification.wav')
    a.volume = volume / 100
    a.play().catch(() => _playFallbackBeep(volume / 100))
    setTimeout(() => a.pause(), 2500)
  } catch {
    /* no audio */
  }
}

/**
 * "Send me a test alert": the ring, the tab and a notification, as the
 * current settings would do them, for four seconds. Never touches a real
 * alert: while a chat rings it does nothing (returns null), and a chat that
 * arrives during the test ends the test.
 */
export function testAlert(seconds = 4): { rang: boolean; flashed: boolean; notified: boolean } | null {
  if (_alertsActive) return null
  _stopTest?.()
  const prefs = readAlertPrefs()
  let audio: HTMLAudioElement | null = null
  let soundTimer: ReturnType<typeof setInterval> | null = null
  let flash: ReturnType<typeof setInterval> | null = null
  const rang = prefs.ring && prefs.volume > 0
  if (rang) {
    if (prefs.sound === 'classic') {
      try {
        audio = new Audio('/notification.wav')
        audio.loop = true
        audio.volume = prefs.volume / 100
        audio.play().catch(() => _playFallbackBeep(prefs.volume / 100))
      } catch {
        /* no audio */
      }
    } else {
      const sound = prefs.sound
      _playNotes(sound, prefs.volume / 100)
      soundTimer = setInterval(() => _playNotes(sound, prefs.volume / 100), RING_REPEAT_MS)
    }
  }
  if (prefs.flash) {
    flash = setInterval(() => {
      document.title = document.title === _baseTitle ? _flashTitle : _baseTitle
    }, 800)
  }
  let notified = false
  if ('Notification' in window && Notification.permission === 'granted') {
    try {
      const n = new Notification('Test · New chat for you', {
        body: 'This is how a new chat shows up. Nothing is waiting.',
        icon: LOGO_SRC,
        tag: 'bbc-assign-test',
      })
      n.onclick = () => {
        window.focus()
        n.close()
      }
      notified = true
    } catch {
      /* */
    }
  }
  const stop = () => {
    if (audio) audio.pause()
    if (soundTimer) clearInterval(soundTimer)
    if (flash) clearInterval(flash)
    // A real alert that cut the test in owns the title now.
    if (!_alertsActive) document.title = _baseTitle
    clearTimeout(timer)
    if (_stopTest === stop) _stopTest = null
  }
  const timer = setTimeout(stop, seconds * 1000)
  _stopTest = stop
  return { rang, flashed: prefs.flash, notified }
}

/** The browser's answer about notifications, for Settings › Alerts. */
export function notifyPermission(): NotifyPermission {
  if (typeof window === 'undefined' || !('Notification' in window)) return 'unsupported'
  return Notification.permission
}

/** "Allow notifications" — only ever from that button's click. */
export async function askNotifyPermission(): Promise<NotifyPermission> {
  if (!('Notification' in window)) return 'unsupported'
  try {
    return await Notification.requestPermission()
  } catch {
    return Notification.permission
  }
}

export function isAssignmentAlertActive(): boolean {
  return _alertsActive
}

export function requestNotifyPermission(): void {
  if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission().catch(() => {})
  }

  // Unlock audio autoplay on first user interaction (click/tap/keypress).
  // Chrome blocks Audio.play() without prior gesture — this invisibly
  // unblocks it on the first click ANYWHERE on the page.
  if (!_audioUnlocked) {
    const _unlock = () => {
      _audioUnlocked = true
      try {
        // Pre-load ring audio so it's ready for instant playback
        if (!_ringAudio) {
          _ringAudio = new Audio('/notification.wav')
          _ringAudio.loop = true
        }
        // Touch play+pause to satisfy autoplay policy
        _ringAudio.volume = 0
        _ringAudio.play()
          .then(() => {
            _ringAudio!.pause()
            _ringAudio!.currentTime = 0
            _ringAudio!.volume = 1
          })
          .catch(() => {})
        // Also unlock AudioContext for beep fallback
        if (!_beepCtx) _beepCtx = new AudioContext()
        if (_beepCtx.state === 'suspended') _beepCtx.resume()
      } catch {
        // Audio not available
      }
      document.removeEventListener('click', _unlock)
      document.removeEventListener('keydown', _unlock)
    }
    document.addEventListener('click', _unlock, { once: false })
    document.addEventListener('keydown', _unlock, { once: false })
  }
}

/** Same visibility rule as ReadyToggle — hands-on operators only. */
export function canReceiveAssignNotifications(role: string | undefined): boolean {
  if (!role) return false
  // Only QA excluded — management needs to hear assignments for oversight
  return !['qa'].includes(role)
}

export function clearAssignmentBadge(): void {
  stopAssignmentAlerts()
}
