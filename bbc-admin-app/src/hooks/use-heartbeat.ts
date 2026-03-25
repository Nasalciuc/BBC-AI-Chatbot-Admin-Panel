import { useEffect, useRef } from 'react'
import { apiFetch } from '@/lib/api'

/**
 * Sends POST /api/agent/heartbeat every `intervalMs` milliseconds.
 * Pauses when the browser tab is not visible.
 * Runs in AuthenticatedLayout — active on ALL admin pages.
 * Failure is silent (agent just won't appear as online).
 */
export function useHeartbeat(intervalMs = 30_000) {
  const active = useRef(true)

  useEffect(() => {
    active.current = true

    const ping = async () => {
      if (!active.current || document.visibilityState !== 'visible') return
      try {
        await apiFetch('/api/agent/heartbeat', { method: 'POST' })
      } catch {
        // Heartbeat failure is non-fatal — agent won't appear online
      }
    }

    ping() // Immediate first ping on mount (operator just logged in)
    const id = setInterval(ping, intervalMs)

    return () => {
      active.current = false
      clearInterval(id)
    }
  }, [intervalMs])
}
