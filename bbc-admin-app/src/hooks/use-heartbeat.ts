import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { apiFetch, getConversations } from '@/lib/api'
import {
  canReceiveAssignNotifications,
  notifyAssignment,
  stopAssignmentAlerts,
  playQueueChime,
} from '@/lib/notify-assignment'
import { useAttentionStore } from '@/stores/attention-store'
import { usePanelModeStore } from '@/stores/panel-mode-store'
import { useQueueStore } from '@/stores/queue-store'
import { reportAttentionCycle, reportPresence, reportQueue } from '@/lib/crm-bridge'
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
  /** Shared queue, scoped to this operator (tunnel/team). Same value that
   *  feeds the CRM's chat:queue badge — one source, no contradictions. */
  queue_count?: number
  queue_ids?: string[]
  /** The longest-waiting conversation in the line — never the client's words. */
  queue_oldest?: {
    id: string
    waiting_seconds: number | null
    route: string | null
  } | null
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
  /** Conversations already toasted this attention episode — one toast each,
   *  not one per 5s poll. Cleared when the alert fully stops. */
  const toasted = useRef<Set<string>>(new Set())
  /** Last seen message_count per conversation, to detect new incoming messages. */
  const seenCounts = useRef<Map<string, number>>(new Map())
  const baselineTaken = useRef(false)
  /** Queue conversations already chimed about — one sound each, not one per 5s poll. */
  const announcedQueue = useRef<Set<string>>(new Set())
  /** Kept in a ref so the polling loop always reads the current selection. */
  const viewingRef = useRef<string | null>(viewingConversationId ?? null)
  // The dormant effect reconfigures the RUNNING worker; it must not be in the
  // main effect's deps or a mode change would tear the worker down and lose
  // the token.
  const workerRef = useRef<Worker | null>(null)
  // Last fallback ping (no-Worker path only) — lets the dormant cadence be
  // enforced inside `ping` itself, since no interval object exists to retime.
  const lastFallbackPing = useRef(0)

  viewingRef.current = viewingConversationId ?? null

  useEffect(() => {
    active.current = true

    const processResponse = (res: HeartbeatResponse) => {
      if (typeof res?.is_ready === 'boolean') {
        setReady(res.is_ready)
        // The CRM shows the agent's own state next to the chat button.
        reportPresence(res.is_ready)
      }
      // Shared queue, riding on the same beat — no second poll.
      if (Array.isArray(res?.queue_ids)) {
        const ids = res.queue_ids
        useQueueStore.getState().setQueue(ids, res.queue_count ?? ids.length)
        // One chime per conversation per episode, the `attended` pattern:
        // the sound says "the line grew", the badge says by how much.
        let isNew = false
        for (const id of ids) {
          if (!announcedQueue.current.has(id)) {
            announcedQueue.current.add(id)
            isNew = true
          }
        }
        if (isNew && ids.length > 0) playQueueChime()
        // The CRM's badge rides the same number — one source, no contradictions.
        // The oldest waiting conversation travels with it: which one, how long,
        // and the route the pipeline extracted. Never the client's own words.
        reportQueue(res.queue_count ?? ids.length, {
          conversationId: res.queue_oldest?.id,
          waitingSeconds: res.queue_oldest?.waiting_seconds ?? undefined,
          route: res.queue_oldest?.route ?? undefined,
        })
        // Conversations that left the line may return later (released):
        // forget them so their return rings again.
        for (const known of announcedQueue.current) {
          if (!ids.includes(known)) announcedQueue.current.delete(known)
        }
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
    workerRef.current = worker

    const ping = async () => {
      if (!active.current) return
      // Worker-less fallback (no Worker support): the cadence effect cannot
      // reconfigure a setInterval it does not own, so the dormant cadence is
      // enforced here instead. Presence still needs a beat, so we skip only
      // the fast ticks: at most one ping per DORMANT_PING_MS while dormant.
      if (usePanelModeStore.getState().dormant) {
        const nowMs = Date.now()
        const gap = useQueueStore.getState().queueCount > 0 ? 5_000 : 15_000
        if (nowMs - lastFallbackPing.current < gap - 250) return
        lastFallbackPing.current = nowMs
      }
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
      // A dormant panel asks for nothing. This runs on its own interval,
      // separate from the worker the cadence effect reconfigures — which is
      // how a "dormant" panel kept a five-second conversations poll alive and
      // undercut the whole point of this branch. Read at call time, never
      // captured: the effect's deps deliberately exclude `dormant`.
      if (usePanelModeStore.getState().dormant) return
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
        // Read before the loop flips it: on the baseline pass the CRM must
        // get the count but must NOT be told to jump in front of the agent.
        const baselineAlreadyTaken = baselineTaken.current
        // The list arrives ordered by updated_at DESC, so the first new
        // arrival in it is the most recent one. One per cycle, on purpose.
        let firstNewArrivalId: string | null = null

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
            if (isNewArrival && firstNewArrivalId === null) firstNewArrivalId = conv.id
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

        // The list page highlights exactly these rows.
        useAttentionStore.getState().setAttentionIds([...needsAttention])

        // Same set, said out loud to the parent frame. No new fetch, no new
        // state, no PII — see src/lib/crm-bridge.ts.
        reportAttentionCycle({
          attentionIds: [...needsAttention],
          // Server truth, and the only part that survives a reload: the
          // backend widens assigned_to=me/status=active to include
          // `needs_agent`, which means reserved-for-me with the client still
          // waiting for a first human reply.
          needsAgentIds: mine
            .filter((c) => c.status === 'needs_agent')
            .map((c) => c.id),
          liveIds: [...liveIds],
          viewingId: viewing,
          newArrivalId: firstNewArrivalId,
          baselineTaken: baselineAlreadyTaken,
        })

        if (needsAttention.size > 0) {
          notifyAssignment()
          // One toast per conversation per episode — the ring says "something
          // needs you", the toast says WHICH chat and takes you there.
          for (const id of needsAttention) {
            if (toasted.current.has(id)) continue
            toasted.current.add(id)
            const conv = mine.find((c) => c.id === id)
            const label =
              conv?.chat_number != null ? `#${conv.chat_number}` : 'a client'
            toast(`New chat assigned — ${label}`, {
              duration: 10_000,
              action: {
                label: 'Open',
                onClick: () => {
                  // Full navigation on purpose: the list reads ?highlight=
                  // at mount, and the toast can fire on any admin page.
                  window.location.assign(`/chats?highlight=${id}`)
                },
              },
            })
          }
        } else {
          stopAssignmentAlerts()
          toasted.current.clear()
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
        workerRef.current = null
      }
    }
  }, [intervalMs, role, setReady, viewingConversationId, queryClient])

  const dormant = usePanelModeStore((s) => s.dormant)
  const queueCount = useQueueStore((s) => s.queueCount)
  const wasDormant = useRef(false)
  useEffect(() => {
    const w = workerRef.current
    // Dormant with an empty queue is the 90% case. Dormant with someone
    // waiting keeps the normal cadence so chat:queue — which rides the
    // heartbeat response — still reaches the CRM within seconds.
    const ms = dormant ? (queueCount > 0 ? 5_000 : 15_000) : intervalMs
    if (w) w.postMessage({ type: 'setInterval', ms })
    if (wasDormant.current && !dormant) {
      if (w) w.postMessage({ type: 'pingNow' })
      // The attention loop skipped every tick while dormant, so its view of
      // "which chats need me" is as old as the dormancy. Refetch once on wake
      // rather than showing a stale alert state until the next interval.
      void queryClient.invalidateQueries({ queryKey: ATTENTION_QUERY_KEY })
    }
    wasDormant.current = dormant
  }, [dormant, queueCount, intervalMs])
}
