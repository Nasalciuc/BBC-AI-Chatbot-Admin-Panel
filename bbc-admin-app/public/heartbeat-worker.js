/**
 * Heartbeat Web Worker — runs at 5s interval even when tab is
 * in background (Chrome throttles main-thread setInterval to 60s+
 * but does NOT throttle Web Workers).
 *
 * Posts heartbeat response to main thread via postMessage.
 */

let _interval = null
let _apiBase = ''
let _token = ''
let _viewingConversationId = null
const HEARTBEAT_MS = 5000

self.onmessage = function (e) {
  const msg = e.data
  if (msg.type === 'start') {
    _apiBase = msg.apiBase || ''
    _token = msg.token || ''
    _viewingConversationId = msg.viewingConversationId || null
    if (_interval) clearInterval(_interval)
    _interval = setInterval(doHeartbeat, HEARTBEAT_MS)
    doHeartbeat() // immediate first ping
  } else if (msg.type === 'stop') {
    if (_interval) clearInterval(_interval)
    _interval = null
  } else if (msg.type === 'updateToken') {
    _token = msg.token || ''
  } else if (msg.type === 'updateViewing') {
    _viewingConversationId = msg.viewingConversationId || null
  } else if (msg.type === 'setInterval') {
    // Dormant mode (panel hidden in the CRM dock, or a background tab) slows
    // the heartbeat without tearing the worker down: terminate()+new Worker
    // would lose the token and re-fire the immediate first ping.
    var ms = Number(msg.ms) || HEARTBEAT_MS
    if (_interval) clearInterval(_interval)
    _interval = setInterval(doHeartbeat, ms)
  } else if (msg.type === 'pingNow') {
    // Leaving dormant: refresh presence and queue at once instead of waiting
    // up to 15s for the next slow tick.
    doHeartbeat()
  }
}

async function doHeartbeat() {
  if (!_apiBase || !_token) return
  try {
    const res = await fetch(_apiBase + '/api/agent/heartbeat', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer ' + _token,
      },
      body: JSON.stringify({ viewing_conversation_id: _viewingConversationId }),
    })
    if (res.ok) {
      const data = await res.json()
      self.postMessage({ type: 'heartbeat', data: data })
    } else {
      self.postMessage({ type: 'error', status: res.status })
    }
  } catch (err) {
    self.postMessage({ type: 'error', message: String(err) })
  }
}
