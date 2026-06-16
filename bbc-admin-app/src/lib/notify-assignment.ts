/**
 * Persistent assignment alerts — rings like a phone until operator
 * opens conversation or 30s timeout fires (AI takes over).
 */

let _ringAudio: HTMLAudioElement | null = null
let _flashInterval: ReturnType<typeof setInterval> | null = null
let _alertsActive = false
const _baseTitle = 'BBC Admin Panel'

export function notifyAssignment(info?: { name?: string }): void {
  _alertsActive = true

  // 1. Sound LOOP
  try {
    if (!_ringAudio) {
      _ringAudio = new Audio('/notification.wav')
      _ringAudio.loop = true
    }
    _ringAudio.currentTime = 0
    _ringAudio.play().catch(() => {})
  } catch {
    /* no audio */
  }

  // 2. Title FLASH every 800ms
  if (!_flashInterval) {
    _flashInterval = setInterval(() => {
      document.title =
        document.title === _baseTitle
          ? '🔔 NEW CHAT! — BBC Admin Panel'
          : _baseTitle
    }, 800)
  }

  // 3. Browser notification
  if ('Notification' in window && Notification.permission === 'granted') {
    try {
      const n = new Notification('🔔 NEW CHAT — respond in 30 seconds!', {
        body: info?.name ? `Client: ${info.name}` : 'A client is waiting!',
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

export function stopAssignmentAlerts(): void {
  _alertsActive = false
  if (_ringAudio) {
    _ringAudio.pause()
    _ringAudio.currentTime = 0
  }
  if (_flashInterval) {
    clearInterval(_flashInterval)
    _flashInterval = null
  }
  document.title = _baseTitle
}

export function isAssignmentAlertActive(): boolean {
  return _alertsActive
}

export function requestNotifyPermission(): void {
  if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission().catch(() => {})
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
