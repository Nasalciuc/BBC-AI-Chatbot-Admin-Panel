import { create } from 'zustand'
import type { LivePass } from '@/lib/types'

/**
 * What the heartbeat hands out every beat for "On the site now": the pass to bbc-live, fresh for 60 s (an auth
 * value). Written by use-heartbeat.ts only; null means "the server says nothing": the feature is off for me.
 * The follow-up lottery's badge rides the same beat but is server data: it lives in the query cache
 * (hooks/use-followups-new.ts).
 */
interface LiveState {
  pass: LivePass | null
  setFromBeat: (pass: LivePass | null) => void
}

export const useLiveStore = create<LiveState>()((set) => ({
  pass: null,
  setFromBeat: (pass) => set({ pass }),
}))
