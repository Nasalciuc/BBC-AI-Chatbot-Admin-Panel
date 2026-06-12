import { useEffect, useRef } from 'react'
import { apiFetch } from '@/lib/api'
import {
  canReceiveAssignNotifications,
  notifyAssignment,
} from '@/lib/notify-assignment'
import { useAuthStore } from '@/stores/auth-store'
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
// Heartbeat runs even when the tab is hidden: browsers throttle
// background intervals to ~60s, which is WITHIN the 120s eligibility
// window — so a logged-in operator stays assignable while working in
// another tab. Pausing made operators silently offline (~10 min) the
// moment they switched windows. (TODO resilience note resolved.)
const HEARTBEAT_INTERVAL_MS = 5_000

type HeartbeatResponse = {
  is_ready?: boolean
  /** Conversations auto-assigned during this ping (0 or 1). */
  assigned?: number
}

/**
 * Sends POST /api/agent/heartbeat every `intervalMs` milliseconds.
 * Runs in AuthenticatedLayout — active on ALL admin pages.
 * Failure is silent (agent just won't appear as online).
 */
export function useHeartbeat(intervalMs = HEARTBEAT_INTERVAL_MS) {
  const active = useRef(true)
  const setReady = useReadyStore((s) => s.setReady)
  const role = useAuthStore((s) => s.auth.user?.role)
  const assignedInitialized = useRef(false)

  useEffect(() => {
    active.current = true

    const ping = async () => {
      if (!active.current) return
      try {
        const res = await apiFetch<HeartbeatResponse>('/api/agent/heartbeat', {
          method: 'POST',
        })
        if (typeof res?.is_ready === 'boolean') {
          setReady(res.is_ready)
        }
        if (
          canReceiveAssignNotifications(role) &&
          typeof res?.assigned === 'number'
        ) {
          // First response after mount seeds the ref — no login blast.
          if (assignedInitialized.current && res.assigned > 0) {
            notifyAssignment()
          }
          assignedInitialized.current = true
        }
      } catch {
        // Heartbeat failure is non-fatal — agent won't appear online
      }
    }

    ping()
    const id = setInterval(ping, intervalMs)

    return () => {
      active.current = false
      clearInterval(id)
    }
  }, [intervalMs, role, setReady])
}
