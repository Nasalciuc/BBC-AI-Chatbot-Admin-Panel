import { create } from 'zustand'

/**
 * The shared queue, as the heartbeat reports it — conversations waiting for
 * ANY operator, not assigned to anyone yet. Written by use-heartbeat.ts from
 * the heartbeat response (`queue_ids` / `queue_count`), read by the chats page
 * and the "In asteptare" badge.
 *
 * Deliberately a SEPARATE store from attention-store: "mine that need me" and
 * "nobody's yet, first click wins" are different things, and merging them
 * would eventually merge them in someone's head too.
 */
interface QueueState {
  queueIds: string[]
  queueCount: number
  setQueue: (ids: string[], count: number) => void
}

export const useQueueStore = create<QueueState>()((set) => ({
  queueIds: [],
  queueCount: 0,
  setQueue: (queueIds, queueCount) => set({ queueIds, queueCount }),
}))
