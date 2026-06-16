// Single place for "you got a chat" signals: looping ring + title flash +
// browser notification. Heartbeat is the trigger (every 5s, any page).

let _ringAudio: HTMLAudioElement | null = null
let _flashInterval: ReturnType<typeof setInterval> | null = null
const _baseTitle = 'BBC Admin Panel'
let _alertsActive = false

export function notifyAssignment(info?: { name?: string }) {
  _alertsActive = true

  // 1. Ring continuously until operator opens the conversation or timeout.
  try {
    if (!_ringAudio) {
      _ringAudio = new Audio('/notification.wav')
      _ringAudio.loop = true
    }
    _ringAudio.play().catch(() => {})
  } catch {
    // Autoplay policy or missing asset — non-fatal
  }

  // 2. Flash title until operator focuses the conversation.
  if (!_flashInterval) {
    _flashInterval = setInterval(() => {
      document.title =
        document.title === _baseTitle
          ? '🔔 NEW CHAT! — BBC Admin Panel'
          : _baseTitle
    }, 800)
  }

  // 3. Browser notification (if permitted).
  if ('Notification' in window && Notification.permission === 'granted') {
    const n = new Notification('🔔 NEW CHAT — respond in 30 seconds!', {
      body: info?.name ? `Client: ${info.name}` : 'A client is waiting!',
      tag: 'bbc-assign',
      requireInteraction: true,
    })
    n.onclick = () => {
      window.focus()
      window.location.assign('/chats')
      n.close()
    }
  }
}

export function stopAssignmentAlerts() {
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

/** @deprecated Use stopAssignmentAlerts */
export function clearAssignmentBadge() {
  stopAssignmentAlerts()
}

export function isAssignmentAlertActive(): boolean {
  return _alertsActive
}

export function requestNotifyPermission() {
  if ('Notification' in window && Notification.permission === 'default') {
    Notification.requestPermission().catch(() => {})
  }
}

/** Same visibility rule as ReadyToggle — hands-on operators only. */
export function canReceiveAssignNotifications(role: string | undefined): boolean {
  if (!role) return false
  return !['owner', 'admin', 'dev', 'supervisor', 'qa'].includes(role)
}
