import { create } from 'zustand'

/** The CRM sign-in (`?sso_token=`), as the sign-in page shows it: pending
 *  while the exchange runs, failed with what the server said. */
type SsoPhase = 'idle' | 'pending' | 'failed'

interface SsoState {
  phase: SsoPhase
  /** HTTP status of the failed exchange; null when the server wasn't reached. */
  status: number | null
  /** The server's reason ("SSO token expired…", "No matching BBC account…"). */
  detail: string | null
  /** The e-mail the CRM token carries, for "The CRM knows you as …". */
  email: string | null
  pending: (email: string | null) => void
  failed: (status: number | null, detail: string | null) => void
  done: () => void
}

export const useSsoStore = create<SsoState>()((set) => ({
  phase: 'idle',
  status: null,
  detail: null,
  email: null,
  pending: (email) => set({ phase: 'pending', status: null, detail: null, email }),
  failed: (status, detail) => set({ phase: 'failed', status, detail }),
  done: () => set({ phase: 'idle', status: null, detail: null }),
}))
