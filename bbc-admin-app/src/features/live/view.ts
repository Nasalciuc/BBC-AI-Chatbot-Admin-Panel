/**
 * "On the site now" (contracts/live.md), the words and the rules of the page; no React, so the tests run it.
 * A visitor is anonymous here: a page, the page's trip (codes and dates), where they came from, the screen.
 */

import type { LiveClaimState, LiveVisitor } from '@/lib/types'

export const HELLO_MAX = 300

/** "3 min", "45 s", "1 h 05". */
export function onSite(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds || 0))
  if (s < 60) return `${s} s`
  if (s < 3600) return `${Math.floor(s / 60)} min`
  const m = Math.floor((s % 3600) / 60)
  return `${Math.floor(s / 3600)} h ${String(m).padStart(2, '0')}`
}

function day(iso?: string): string {
  if (!iso || !/^\d{4}-\d{2}-\d{2}$/.test(iso)) return ''
  const d = new Date(`${iso}T12:00:00Z`)
  return d.toLocaleDateString('en-US', { day: 'numeric', month: 'short', timeZone: 'UTC' })
}

/** "JFK → IST · 14 Dec – 2 Jan · business · 2 travellers", or "" when the page names no trip. */
export function tripLine(v: Pick<LiveVisitor, 'trip'>): string {
  const t = v.trip
  if (!t) return ''
  const route = t.from || t.to ? `${t.from || '?'} → ${t.to || '?'}` : ''
  const dates = [day(t.depart), day(t.return)].filter(Boolean).join(' – ')
  const pax = t.pax ? `${t.pax} traveller${t.pax === 1 ? '' : 's'}` : ''
  return [route, dates, t.cabin || '', pax].filter(Boolean).join(' · ')
}

export const SOURCE_LABEL: Record<string, string> = { kayak: 'Kayak', ads: 'Ad', site: 'Site' }

export function sourceLabel(source: string): string {
  return SOURCE_LABEL[source] ?? 'Site'
}

/** Who the visitor is to me: free to greet, mine, a colleague's, said not now, or in a chat already. */
export type RowState = 'free' | 'mine' | 'taken' | 'declined' | 'in_chat'

export function rowState(v: LiveVisitor, me: string | undefined): RowState {
  if (v.has_chat) return 'in_chat'
  if (v.dismissed) return 'declined'
  if (v.claimed_by) return v.claimed_by === me ? 'mine' : 'taken'
  return 'free'
}

export const ROW_LABEL: Record<RowState, string> = {
  free: 'Nobody yet',
  mine: 'Yours: say hello',
  taken: 'A colleague is on it',
  declined: 'Said not now',
  in_chat: 'In a chat',
}

/** How many visitors this agent holds now (the server stops a claim at the most). */
export function heldBy(rows: LiveVisitor[], me: string | undefined): number {
  return me ? rows.filter((v) => v.claimed_by === me && !v.has_chat).length : 0
}

/** Longest on the site first; within that, the ones nobody holds before the rest. */
export function ordered(rows: LiveVisitor[], me: string | undefined): LiveVisitor[] {
  const rank: Record<RowState, number> = { mine: 0, free: 1, taken: 2, declined: 3, in_chat: 4 }
  return [...rows].sort((a, b) => rank[rowState(a, me)] - rank[rowState(b, me)] || b.seconds_on_site - a.seconds_on_site)
}

/** The answer to a claim, in words; null when it worked. */
export function claimProblem(state: LiveClaimState): string | null {
  switch (state) {
    case 'claimed':
    case 'mine':
      return null
    case 'taken':
      return 'A colleague took this visitor a moment ago.'
    case 'full':
      return 'You hold 3 visitors already: say hello to one, or let one go.'
    case 'declined':
      return 'This visitor said not now to a hello. Nobody writes to them for an hour.'
    case 'in_a_chat':
      return 'This visitor is in a chat already: it is in the list of chats.'
    case 'gone':
      return 'This visitor left the site.'
  }
}

/** What's wrong with a hello before it goes, or null. The server checks again (and for contacts). */
export function helloProblem(text: string): string | null {
  const t = text.trim()
  if (!t) return 'Write a few words first.'
  if (t.length > HELLO_MAX) return `At most ${HELLO_MAX} characters (${t.length} now).`
  if (/https?:\/\/|www\./i.test(t)) return 'No links in a first message.'
  if (/[^\s@]+@[^\s@]+\.[a-z]{2,}/i.test(t)) return 'No e-mail address in a first message: the chat carries it.'
  if ((t.match(/\d/g) || []).length >= 7) return 'No phone number in a first message: the chat carries it.'
  return null
}

/** A first line to start from, built from what the page says (the agent edits it). */
export function suggestedHello(v: LiveVisitor, agentFirstName: string): string {
  const who = agentFirstName ? `Hi, I'm ${agentFirstName} from the travel team.` : 'Hi, I am from the travel team.'
  const t = v.trip
  if (t?.to) return `${who} Looking at flights to ${t.to}? I can check business class fares for your dates.`
  return `${who} Can I help you find a business class fare?`
}
