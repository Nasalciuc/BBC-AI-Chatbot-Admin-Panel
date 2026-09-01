import { isEmbedded } from '@/lib/crm-bridge'
import { usePanelModeStore } from '@/stores/panel-mode-store'

/**
 * One consumer per human.
 *
 * Operators keep the CRM tab open (panel embedded in the dock) AND a
 * standalone panel tab "to see better" — two full pollers for one person.
 * The embedded panel is the leader; a standalone panel that hears it goes
 * dormant and says so. If the leader goes quiet for 15s (CRM tab closed),
 * the standalone panel resumes on its own — no handshake, no lock to leak.
 */
const CHANNEL = 'bbc-panel'
const BEAT_MS = 5_000
const LEADER_TIMEOUT_MS = 15_000

export function installPanelLeader(): () => void {
  if (typeof BroadcastChannel === 'undefined') return () => {}
  const ch = new BroadcastChannel(CHANNEL)
  const set = usePanelModeStore.getState().setReason
  let beat: ReturnType<typeof setInterval> | null = null
  let watchdog: ReturnType<typeof setTimeout> | null = null

  if (isEmbedded()) {
    const announce = () => ch.postMessage({ type: 'leader' })
    announce()
    beat = setInterval(announce, BEAT_MS)
  } else {
    ch.onmessage = (e: MessageEvent) => {
      if ((e.data as { type?: string } | null)?.type !== 'leader') return
      set('not_leader', true)
      if (watchdog) clearTimeout(watchdog)
      watchdog = setTimeout(() => set('not_leader', false), LEADER_TIMEOUT_MS)
    }
  }

  return () => {
    if (beat) clearInterval(beat)
    if (watchdog) clearTimeout(watchdog)
    set('not_leader', false)
    ch.close()
  }
}
