import { create } from 'zustand'

// Active-team context filter. `null` = "All teams" (no focus; the user sees
// everything their role already allows). In-memory only — mirrors auth-store,
// which persists just the token (via cookie), not arbitrary UI state, so the
// active-team focus resets on reload by design.
interface TeamState {
  activeTeamId: string | null
  setActiveTeam: (id: string | null) => void
}

export const useTeamStore = create<TeamState>()((set) => ({
  activeTeamId: null,
  setActiveTeam: (id) => set({ activeTeamId: id }),
}))
