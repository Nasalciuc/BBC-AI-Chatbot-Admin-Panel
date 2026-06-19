import { useEffect, useRef } from 'react'
import { apiFetch } from '@/lib/api'
import {
  canReceiveAssignNotifications,
  isAssignmentAlertActive,
  notifyAssignment,
  stopAssignmentAlerts,
} from '@/lib/notify-assignment'
import { useAuthStore } from '@/stores/auth-store'
import { useReadyStore } from '@/stores/ready-store'

const HEARTBEAT_INTERVAL_MS = 5_000

type HeartbeatResponse = {
  is_ready?: boolean
  /** Conversations auto-assigned during this ping (0 or 1). */
  assigned?: number
  /** Active human-mode conversations assigned to this operator. */
  active_assigned?: number
}

/**
 * Sends POST /api/agent/heartbeat every `intervalMs` milliseconds.
 * Runs in AuthenticatedLayout — active on ALL admin pages.
 */
export function useHeartbeat(intervalMs = HEARTBEAT_INTERVAL_MS, viewingConversationId?: string | null) {
  const active = useRef(true)
  const setReady = useReadyStore((s) => s.setReady)
  const role = useAuthStore((s) => s.auth.user?.role)
  const assignedInitialized = useRef(false)
  const prevActiveAssigned = useRef(0)

  useEffect(() => {
    active.current = true

    const processResponse = (res: HeartbeatResponse) => {
      if (typeof res?.is_ready === 'boolean') {
        setReady(res.is_ready)
      }
      if (
        canReceiveAssignNotifications(role) &&
        typeof res?.active_assigned === 'number'
      ) {
        const activeCount = res.active_assigned
        if (assignedInitialized.current) {
          if (activeCount > prevActiveAssigned.current) {
            notifyAssignment()
          } else if (
            activeCount === 0 &&
            prevActiveAssigned.current > 0 &&
            isAssignmentAlertActive()
          ) {
            stopAssignmentAlerts()
          }
        }
        prevActiveAssigned.current = activeCount
        assignedInitialized.current = true
      } else if (
        canReceiveAssignNotifications(role) &&
        typeof res?.assigned === 'number'
      ) {
        if (assignedInitialized.current && res.assigned > 0) {
          notifyAssignment()
        }
        assignedInitialized.current = true
      }
    }

    const apiBase = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
    const token = useAuthStore.getState().auth.accessToken

    let worker: Worker | null = null
    try {
      worker = new Worker('/heartbeat-worker.js')
    } catch {
      // Worker not supported — fall back to setInterval below
    }

    if (worker && token) {
      worker.postMessage({ type: 'start', apiBase, token, viewingConversationId: viewingConversationId || null })

      worker.onmessage = (e: MessageEvent) => {
        if (!active.current || e.data.type !== 'heartbeat') return
        processResponse(e.data.data as HeartbeatResponse)
      }

      return () => {
        active.current = false
        worker?.postMessage({ type: 'stop' })
        worker?.terminate()
      }
    }

    const ping = async () => {
      if (!active.current) return
      try {
        const res = await apiFetch<HeartbeatResponse>('/api/agent/heartbeat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ viewing_conversation_id: viewingConversationId || null }),
        })
        processResponse(res)
      } catch {
        // Heartbeat failure is non-fatal
      }
    }

    ping()
    const id = setInterval(ping, intervalMs)

    return () => {
      active.current = false
      clearInterval(id)
    }
  }, [intervalMs, role, setReady, viewingConversationId])
}
