import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getNotifications } from '@/lib/api'
import { notifyTaskReminder } from '@/lib/notify-assignment'
import { usePanelModeStore } from '@/stores/panel-mode-store'
import { dueReminders, localZone } from './view'

const SEEN_KEY = 'bbc_task_reminders'
const KEEP_MS = 2 * 86_400_000

/** The tasks part of /api/notifications — the same query (and poll) as the bell. */
export function useTaskAlerts() {
  const dormant = usePanelModeStore((s) => s.dormant)
  return useQuery({
    queryKey: ['notifications'],
    queryFn: getNotifications,
    refetchInterval: dormant ? false : 30_000,
    select: (d) => d.tasks,
  })
}

function readSeen(): Record<string, number> {
  try {
    const v: unknown = JSON.parse(localStorage.getItem(SEEN_KEY) || '{}')
    return v && typeof v === 'object' ? (v as Record<string, number>) : {}
  } catch {
    return {}
  }
}

function writeSeen(seen: Record<string, number>): void {
  try {
    localStorage.setItem(SEEN_KEY, JSON.stringify(seen))
  } catch {
    /* private window: a reminder may come twice, never not at all */
  }
}

/**
 * Reminders: 15 min before and at the due time for your tasks, and 30 min after
 * for a supervisor's team. One panel per person rings (the leader); a dozing
 * tab stays quiet. Each reminder fires once, remembered in this browser.
 */
export function useTaskReminders(): void {
  const dormant = usePanelModeStore((s) => s.dormant)
  const { data } = useTaskAlerts()
  const [tick, setTick] = useState(0)
  useEffect(() => {
    const t = setInterval(() => setTick((n) => n + 1), 30_000)
    return () => clearInterval(t)
  }, [])
  useEffect(() => {
    if (dormant || !data?.set_up || !data.time_zone) return
    const now = Date.now()
    const stored = readSeen()
    for (const [k, at] of Object.entries(stored)) if (now - at > KEEP_MS) delete stored[k]
    const due = dueReminders(data.mine, data.team_late, now, localZone(), new Set(Object.keys(stored)))
    for (const r of due) {
      notifyTaskReminder(r.title, r.body, r.key)
      stored[r.key] = now
    }
    writeSeen(stored)
  }, [data, dormant, tick])
}
