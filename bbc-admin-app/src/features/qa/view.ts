/** The QA page's words (part 3; server: app/services/qa_sample.py). Pure, for the tests. */

import type { QaAgentStats, QaItem, QaVerdict } from '@/lib/types'

export const QA_ROLES = ['qa', 'owner', 'admin', 'dev']

export const VERDICT_LABEL: Record<QaVerdict, string> = { ok: 'Fine', problem: 'A problem', discuss: 'To discuss' }

/** Yesterday in New York, the day the 07:00 sample is about. */
export function yesterdayInNewYork(now = new Date()): string {
  const ny = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }))
  ny.setDate(ny.getDate() - 1)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${ny.getFullYear()}-${pad(ny.getMonth() + 1)}-${pad(ny.getDate())}`
}

/** The day a link names (the QA e-mail opens /qa?day=YYYY-MM-DD) when it is a real calendar day; otherwise
 *  yesterday in New York (CodeRabbit #273: the e-mail of the 20th, opened on the 22nd, opens the 20th). */
export function dayFromLink(raw: unknown, now = new Date()): string {
  if (typeof raw === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(raw)) {
    const d = new Date(`${raw}T12:00:00Z`)
    if (!Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === raw) return raw
  }
  return yesterdayInNewYork(now)
}

/** "3 to check · 1 problem · 4 fine". */
export function countsLine(c: Record<string, number>): string {
  const parts = [
    c.open ? `${c.open} to check` : '',
    c.problem ? `${c.problem} problem${c.problem === 1 ? '' : 's'}` : '',
    c.discuss ? `${c.discuss} to discuss` : '',
    c.ok ? `${c.ok} fine` : '',
  ].filter(Boolean)
  return parts.join(' · ') || 'Nothing picked'
}

/** Why it was picked: the suspicious reasons first, "Random" last. */
export function reasonsLine(item: Pick<QaItem, 'reasons'>): string {
  const rs = [...item.reasons].sort((a, b) => Number(a.code === 'random') - Number(b.code === 'random'))
  return rs.map((r) => r.label).join(' · ')
}

/** One agent's week: "5 checked: 1 problem, 1 to discuss". */
export function statsLine(s: QaAgentStats): string {
  const checked = s.picked - s.open
  const notes = [s.problem ? `${s.problem} problem${s.problem === 1 ? '' : 's'}` : '', s.discuss ? `${s.discuss} to discuss` : '']
    .filter(Boolean)
  return `${s.picked} picked, ${checked} checked${notes.length ? `: ${notes.join(', ')}` : ''}`
}
