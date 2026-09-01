/**
 * Minimal SSE over fetch(): EventSource cannot send Authorization, and we will
 * not put a session JWT in a query string (it lands in proxy logs). Parses
 * `data:` lines, ignores comments (keepalives). Reconnects with backoff
 * 1s→2s→4s→8s (max); after 3 consecutive failures calls onGiveUp so the
 * caller can fall back to polling AT TODAY'S INTERVALS, never faster.
 */
export interface SseHandle {
  close: () => void
}

export function openSse(
  url: string,
  token: string,
  onEvent: (data: unknown) => void,
  onStateChange: (state: 'open' | 'reconnecting' | 'closed') => void,
  onGiveUp: () => void
): SseHandle {
  let closed = false
  let failures = 0
  let ctrl: AbortController | null = null

  const run = async (): Promise<void> => {
    while (!closed) {
      ctrl = new AbortController()
      try {
        const res = await fetch(url, {
          headers: { Authorization: `Bearer ${token}`, Accept: 'text/event-stream' },
          signal: ctrl.signal,
        })
        if (!res.ok || !res.body) throw new Error(`sse ${res.status}`)
        failures = 0
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
        if (failures >= 3) {
          onStateChange('closed')
          onGiveUp()
          return
        }
        onStateChange('reconnecting')
        await new Promise((r) => setTimeout(r, Math.min(8_000, 1_000 * 2 ** (failures - 1))))
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
