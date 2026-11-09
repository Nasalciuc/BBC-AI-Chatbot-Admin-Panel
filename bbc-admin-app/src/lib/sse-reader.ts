/**
 * Minimal SSE over fetch(): EventSource cannot send Authorization, and we will
 * not put a session JWT in a query string (it lands in proxy logs). Parses
 * `data:` lines, ignores comments (keepalives).
 *
 * Reconnects without limit (PR-10), backoff 1s→2s→4s→…→30s (cap). After 3
 * consecutive failures it calls onGiveUp ONCE so the caller can fall back to
 * polling AT TODAY'S INTERVALS, never faster — and keeps trying: the moment
 * the stream reopens, onStateChange('open') lets the caller stop polling. It
 * used to stop for good after three failures, leaving a long-lived panel tab
 * polling for the rest of the shift after one Railway restart.
 */
export const SSE_BACKOFF_CAP_MS = 30_000

export function sseBackoff(failures: number): number {
  return Math.min(SSE_BACKOFF_CAP_MS, 1_000 * 2 ** Math.max(0, failures - 1))
}

export interface SseHandle {
  close: () => void
}

export function openSse(
  url: string,
  // A function is re-read on every (re)connect, so renewed credentials are
  // picked up instead of retrying with expired ones forever (review fix).
  headers: Record<string, string> | (() => Record<string, string>),
  onEvent: (data: unknown) => void,
  onStateChange: (state: 'open' | 'reconnecting' | 'closed') => void,
  onGiveUp: () => void,
  backoff: (failures: number) => number = sseBackoff,
): SseHandle {
  let closed = false
  let failures = 0
  let gaveUp = false
  let ctrl: AbortController | null = null

  const run = async (): Promise<void> => {
    while (!closed) {
      ctrl = new AbortController()
      try {
        const current = typeof headers === 'function' ? headers() : headers
        const res = await fetch(url, {
          headers: { ...current, Accept: 'text/event-stream' },
          signal: ctrl.signal,
        })
        if (!res.ok || !res.body) throw new Error(`sse ${res.status}`)
        failures = 0
        gaveUp = false
        onStateChange('open')
        const reader = res.body.getReader()
        const dec = new TextDecoder()
        let buf = ''
        for (;;) {
          const { value, done } = await reader.read()
          if (done) break
          buf += dec.decode(value, { stream: true })
          let idx: number
          while ((idx = buf.indexOf('\n\n')) >= 0) {
            const frame = buf.slice(0, idx)
            buf = buf.slice(idx + 2)
            const data = frame
              .split('\n')
              .filter((l) => l.startsWith('data:'))
              .map((l) => l.slice(5).trim())
              .join('\n')
            if (data) {
              try {
                onEvent(JSON.parse(data))
              } catch {
                // A frame we cannot parse is one event lost, not a dead stream.
              }
            }
          }
        }
        throw new Error('sse ended')
      } catch {
        if (closed) break
        failures += 1
        if (failures >= 3 && !gaveUp) {
          gaveUp = true
          onGiveUp()
        }
        onStateChange('reconnecting')
        await new Promise((r) => setTimeout(r, backoff(failures)))
      }
    }
  }

  void run()
  return {
    close: () => {
      closed = true
      ctrl?.abort()
      onStateChange('closed')
    },
  }
}
