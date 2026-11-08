import { create } from 'zustand'

interface ReadyState {
  /** null = not yet synced from server (first heartbeat pending) */
  isReady: boolean | null
  setReady: (value: boolean) => void
  viewingConversationId: string | null
  setViewingConversationId: (id: string | null) => void
  /** The first-reply deadline after Take, as the server's sweep enforces it
   *  (heartbeat `response_deadline_seconds`), and the longer one that applies
   *  while the operator has the chat open (`viewing_grace_seconds`). The
   *  defaults hold only until the first heartbeat answers. */
  responseDeadlineSeconds: number
  viewingGraceSeconds: number
  setDeadlines: (responseSeconds: number, viewingSeconds: number) => void
}

export const useReadyStore = create<ReadyState>()((set) => ({
  isReady: null,
  setReady: (value) => set({ isReady: value }),
  viewingConversationId: null,
  setViewingConversationId: (viewingConversationId) => set({ viewingConversationId }),
  responseDeadlineSeconds: 90,
  viewingGraceSeconds: 120,
  setDeadlines: (responseDeadlineSeconds, viewingGraceSeconds) =>
    set({ responseDeadlineSeconds, viewingGraceSeconds }),
}))
