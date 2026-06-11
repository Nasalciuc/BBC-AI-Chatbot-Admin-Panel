import { useEffect, useRef } from 'react'
import { apiFetch } from '@/lib/api'
import { useReadyStore } from '@/stores/ready-store'

// Heartbeat cadence — 5s keeps pickup latency low for auto-assigned
// conversations (each heartbeat triggers _assign_pending_conversations
// backend-side, so operators pick up queued chats within 5s of going
// idle instead of up to 30s).
//
// Load: 10 operators × 12 heartbeats/min = 120 heartbeats/min = ~2/sec
// backend-wide. Each heartbeat = ~3 DB queries. Low impact on free-tier
// Supabase.
//
// TODO (resilience): browser throttles setInterval to 1min in background
// tabs after 5min of inactivity. That means a heartbeat of 5s becomes
// effectively 60s+ for operators whose admin tab is not focused. Proper
// fix: switch to SSE keepalive (server pushes are NOT throttled). Separate
// ticket. Do NOT remove this TODO.
const HEARTBEAT_INTERVAL_MS = 5_000

/**
 * Sends POST /api/agent/heartbeat every `intervalMs` milliseconds.
 * Pauses when the browser tab is not visible.
 * Runs in AuthenticatedLayout — active on ALL admin pages.
 * Failure is silent (agent just won't appear as online).
 */
export function useHeartbeat(intervalMs = HEARTBEAT_INTERVAL_MS) {
  const active = useRef(true)
  const setReady = useReadyStore((s) => s.setReady)

  useEffect(() => {
    active.current = true

    const ping = async () => {
      if (!active.current || document.visibilityState !== 'visible') return
      try {
        const res = await apiFetch<{ is_ready?: boolean }>('/api/agent/heartbeat', { method: 'POST' })
        if (typeof res?.is_ready === 'boolean') {
          setReady(res.is_ready)
        }
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
