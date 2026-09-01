import { create } from 'zustand'

/**
 * Dormant = the operator cannot see this panel right now.
 *
 * Three independent reasons, any one is enough:
 *  - tab_hidden: Page Visibility says the tab is in the background.
 *  - crm_hidden: the CRM told us its dock is minimised. An iframe hidden by
 *    CSS is NOT "hidden" to the browser — the CRM tab is visible — so only
 *    the CRM can tell us. This is the case that mattered: operators live in
 *    the CRM with the panel minimised, polling at full speed all day.
 *  - not_leader: another panel of ours is the leader (BroadcastChannel).
 *
 * Dormant slows the heartbeat to 15s (5s while the queue is non-empty, so
 * chat:queue still reaches the CRM promptly) and stops every other poll.
 * Nothing anywhere gets FASTER than today.
 */
type Reason = 'tab_hidden' | 'crm_hidden' | 'not_leader'

interface PanelModeState {
  reasons: Set<Reason>
  dormant: boolean
  setReason: (reason: Reason, on: boolean) => void
}

export const usePanelModeStore = create<PanelModeState>((set) => ({
  reasons: new Set<Reason>(),
  dormant: false,
  setReason: (reason, on) =>
    set((s) => {
      const has = s.reasons.has(reason)
      if (has === on) return s // no-op: avoid needless re-renders
      const next = new Set(s.reasons)
      if (on) next.add(reason)
      else next.delete(reason)
      return { reasons: next, dormant: next.size > 0 }
    }),
}))
