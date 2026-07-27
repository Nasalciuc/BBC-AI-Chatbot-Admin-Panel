import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { apiFetch, getConversations } from '@/lib/api'
import {
  canReceiveAssignNotifications,
  notifyAssignment,
  stopAssignmentAlerts,
} from '@/lib/notify-assignment'
import { useAuthStore } from '@/stores/auth-store'
import { useReadyStore } from '@/stores/ready-store'

const HEARTBEAT_INTERVAL_MS = 5_000

/**
 * The chats page already polls this exact set every 5s for its "My Active" tab,
 * so the attention check shares its React Query key and params instead of
 * issuing a second identical request. Whichever fires first fills the cache and
 * the other reads it — one full-list fetch per interval instead of two.
 *
 * MUST stay in sync with the list's queryKey in features/chats/index.tsx
 * (['conversations', tab, search, tunnel, handledBy]) and its params.
 */
export const ATTENTION_QUERY_KEY = ['conversations', 'my_active', '', '', 'all']
const ATTENTION_PARAMS = { assigned_to: 'me', status: 'active', limit: '50' }

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
 *
 * Also drives the assignment alert. The alert rings for conversations that
 * NEED ATTENTION — newly arrived, or someone wrote in one the operator never
 * opened — and keeps ringing until every one of them has been opened. The
 * heartbeat response only carries counts, so the attention set is derived from
 * the operator's own active conversations instead: a count going up cannot
 * tell an arriving client apart from a re-assignment of a chat already handled.
 */
export function useHeartbeat(intervalMs = HEARTBEAT_INTERVAL_MS, viewingConversationId?: string | null) {
  const active = useRef(true)
  const setReady = useReadyStore((s) => s.setReady)
  const role = useAuthStore((s) => s.auth.user?.role)
  const queryClient = useQueryClient()

  /** Conversations the operator has opened — never ring for these again. */
  const attended = useRef<Set<string>>(new Set())
  /** Last seen message_count per conversation, to detect new incoming messages. */
  const seenCounts = useRef<Map<string, number>>(new Map())
  const baselineTaken = useRef(false)
  /** Kept in a ref so the polling loop always reads the current selection. */
  const viewingRef = useRef<string | null>(viewingConversationId ?? null)

  viewingRef.current = viewingConversationId ?? null

  useEffect(() => {
    active.current = true

    const processResponse = (res: HeartbeatResponse) => {
      if (typeof res?.is_ready === 'boolean') {
        setReady(res.is_ready)
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

    /**
     * Recompute which of the operator's conversations still need attention and
     * start/stop the alert accordingly.
     */
    const refreshAttention = async () => {
      if (!active.current) return
      try {
        // Reads the chats list's cache when it's fresh; fetches itself
        // otherwise (other pages, other tabs) so the alert still works there.
        const res = await queryClient.fetchQuery({
          queryKey: ATTENTION_QUERY_KEY,
          queryFn: () => getConversations(ATTENTION_PARAMS),
          staleTime: Math.max(0, intervalMs - 500),
        })
        if (!active.current) return
        const mine = res?.data ?? []
        const viewing = viewingRef.current
        const needsAttention = new Set<string>()

        for (const conv of mine) {
          const prevCount = seenCounts.current.get(conv.id)
          const count = conv.message_count ?? 0

          // Whatever is open right now counts as handled.
          if (conv.id === viewing) attended.current.add(conv.id)

          if (!baselineTaken.current) {
            // First pass after mount: everything already on screen is the
            // baseline, so logging in never sets the alert off.
            attended.current.add(conv.id)
          } else if (!attended.current.has(conv.id)) {
            const isNewArrival = prevCount === undefined
            const gotNewMessage = prevCount !== undefined && count > prevCount
            if (isNewArrival || gotNewMessage) needsAttention.add(conv.id)
          }

          seenCounts.current.set(conv.id, count)
        }

        // Drop message counts for conversations that left the list, but KEEP
        // them in `attended`: a chat that was handled and later re-assigned
        // must not ring again on its own.
        const liveIds = new Set(mine.map((c) => c.id))
        for (const id of seenCounts.current.keys()) {
          if (!liveIds.has(id)) seenCounts.current.delete(id)
        }

        baselineTaken.current = true

        if (needsAttention.size > 0) {
          notifyAssignment()
        } else {
          stopAssignmentAlerts()
        }
      } catch {
        // Never let the alert loop break the heartbeat
      }
    }

    const alertsEnabled = canReceiveAssignNotifications(role)

    if (worker && token) {
      worker.postMessage({ type: 'start', apiBase, token, viewingConversationId: viewingConversationId || null })

      worker.onmessage = (e: MessageEvent) => {
        if (!active.current || e.data.type !== 'heartbeat') return
        processResponse(e.data.data as HeartbeatResponse)
      }
    } else {
      ping()
    }

    const ids: ReturnType<typeof setInterval>[] = []
    if (!worker || !token) ids.push(setInterval(ping, intervalMs))
    if (alertsEnabled) {
      refreshAttention()
      ids.push(setInterval(refreshAttention, intervalMs))
    }

    return () => {
      active.current = false
      ids.forEach(clearInterval)
      if (worker) {
        worker.postMessage({ type: 'stop' })
        worker.terminate()
      }
    }
  }, [intervalMs, role, setReady, viewingConversationId, queryClient])
}
