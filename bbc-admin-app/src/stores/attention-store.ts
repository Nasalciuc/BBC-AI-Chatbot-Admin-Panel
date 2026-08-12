import { create } from 'zustand'

/**
 * Conversations that currently need the operator's attention — written by
 * the heartbeat's attention loop (use-heartbeat.ts), read by the chats list
 * to highlight the rows that are ringing. A separate store because the
 * heartbeat lives in the layout and the list must not re-derive the set.
 */
interface AttentionState {
  attentionIds: string[]
  setAttentionIds: (ids: string[]) => void
}

export const useAttentionStore = create<AttentionState>()((set) => ({
  attentionIds: [],
  setAttentionIds: (attentionIds) => set({ attentionIds }),
}))
