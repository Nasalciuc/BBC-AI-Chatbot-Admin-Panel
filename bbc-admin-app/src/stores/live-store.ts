import { create } from 'zustand'
import type { LivePass } from '@/lib/types'
import { useAuthStore } from '@/stores/auth-store'

/**
 * What the heartbeat hands out every beat for "On the site now": the pass to bbc-live, fresh for 60 s (an auth
 * value). Written by use-heartbeat.ts only; null means "the server says nothing": the feature is off for me.
 */
interface LiveState {
  pass: LivePass | null
  setFromBeat: (pass: LivePass | null) => void
}

export const useLiveStore = create<LiveState>()((set) => ({
  pass: null,
  setFromBeat: (pass) => set({ pass }),
}))

/** The pass is the session's. Signing out, a 401 and an expired session all reset the auth store, and another
 *  account replaces its user: each clears the pass, so nobody sends the last account's (CodeRabbit #273). */
useAuthStore.subscribe((now, before) => {
  const ended = !now.auth.user && !now.auth.accessToken
  const otherAccount = (now.auth.user?.accountNo ?? '') !== (before.auth.user?.accountNo ?? '')
  if ((ended || otherAccount) && useLiveStore.getState().pass) useLiveStore.getState().setFromBeat(null)
})
