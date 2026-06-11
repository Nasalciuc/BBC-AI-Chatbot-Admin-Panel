import { create } from 'zustand'

interface ReadyState {
  /** null = not yet synced from server (first heartbeat pending) */
  isReady: boolean | null
  setReady: (value: boolean) => void
}

export const useReadyStore = create<ReadyState>()((set) => ({
  isReady: null,
  setReady: (value) => set({ isReady: value }),
}))
