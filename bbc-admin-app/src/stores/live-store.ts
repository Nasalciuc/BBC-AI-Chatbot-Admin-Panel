import { create } from 'zustand'
import type { LivePass } from '@/lib/types'

/**
 * What the heartbeat hands out every beat for "On the site now" (the pass to bbc-live, fresh for 60 s) and the
 * follow-up lottery's badge (leads given to me that I haven't opened). Written by use-heartbeat.ts only; null
 * means "the server says nothing": the feature is off for me, or Redis can't say.
 */
interface LiveState {
  pass: LivePass | null
  followupsNew: number | null
  setFromBeat: (pass: LivePass | null, followupsNew: number | null) => void
}

export const useLiveStore = create<LiveState>()((set) => ({
  pass: null,
  followupsNew: null,
  setFromBeat: (pass, followupsNew) => set({ pass, followupsNew }),
}))
