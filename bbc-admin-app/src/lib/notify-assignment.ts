// Single place for "you got a chat" signals: sound + browser
// notification + title badge. Heartbeat is the trigger (every 5s,
// any page) — the chat calls the operator, not the other way around.

let titleBadged = false
const baseTitle = typeof document !== 'undefined' ? document.title : ''

export function notifyAssignment(info?: { name?: string }) {
  // 1. Sound — reuse the existing asset (same as /chats):
  try {
    new Audio('/notification.wav').play().catch(() => {})
  } catch {
    // Autoplay policy or missing asset — non-fatal
  }
  // 2. Title badge:
  if (!titleBadged) {
    document.title = `🔴 New chat — ${baseTitle}`
    titleBadged = true
  }
  // 3. Browser notification (if permitted):
  if ('Notification' in window && Notification.permission === 'granted') {
    const n = new Notification('New chat assigned', {
      body: info?.name ? `Visitor: ${info.name}` : 'A client is waiting for you',
      tag: 'bbc-assign',
    })
    n.onclick = () => {
      window.focus()
      window.location.assign('/chats')
      n.close()
    }
  }
}

export function clearAssignmentBadge() {
  if (titleBadged) {
    document.title = baseTitle
    titleBadged = false
  }
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
