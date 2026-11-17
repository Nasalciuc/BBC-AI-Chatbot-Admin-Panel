/**
 * The people who take a chat over from Alex: how the widget names and pictures them. Pure, so the
 * rules run under `node --test` (tests/operator.test.mjs).
 */

/** What a person from the team is called when their name is missing, and the word after a name. */
export const CONSULTANT_LABEL = 'Consultant'

/** The fields of a message row these rules read. */
export interface OperatorRow {
  id: string
  role: string
  created_at?: string
  agent_name?: string | null
  agent_avatar_url?: string | null
}

/** A person from the team as the panel shows them. */
export interface Operator {
  name: string
  avatarUrl: string | null
}

/** "Kate Morris" → "KM": what shows when there is no photo. */
export function agentInitials(name: string): string {
  return name
    .split(' ')
    .map(w => w[0] || '')
    .join('')
    .toUpperCase()
    .slice(0, 2)
}

/**
 * Who answers the client now: the person who wrote the latest reply, or null while the latest reply
 * is Alex's (no one took the chat, or it came back to him). The header shows this one (owner,
 * 17 Nov 2026: the client sees that the agent they work with has changed).
 *
 * Latest by the time the reply was written, not by its place in the list: a poll that comes back
 * late appends older rows after newer ones (reconcile.ts), and an old reply of Alex's at the end
 * must not take the header from the person writing now (CodeRabbit #264). Equal times, or a row
 * without one, go by place.
 */
export function currentOperator(rows: readonly OperatorRow[]): Operator | null {
  let latest: OperatorRow | null = null
  let latestAt = -Infinity
  for (const m of rows) {
    if (m.role !== 'ai' && m.role !== 'agent') continue
    const at = Date.parse(m.created_at || '')
    const when = Number.isNaN(at) ? latestAt : at
    if (when >= latestAt) {
      latest = m
      latestAt = when
    }
  }
  if (!latest || latest.role !== 'agent') return null
  return {
    name: (latest.agent_name || '').trim() || CONSULTANT_LABEL,
    avatarUrl: latest.agent_avatar_url || null,
  }
}

/**
 * The same stored message arrived again (the server can publish a row twice): the copy on screen
 * stays, and takes the operator's name and photo from the new one when only the new one has them.
 * 17 Nov 2026: the bare copy came first, the signed one was dropped as a duplicate, and the bubble
 * said "Consultant" with a "C". Returns `had` itself when there is nothing to add.
 */
export function withOperatorFrom<T extends OperatorRow>(had: T, copy: T): T {
  if (had.role !== 'agent') return had
  const name = (had.agent_name || '').trim() ? had.agent_name : (copy.agent_name || had.agent_name)
  const avatar = had.agent_avatar_url || copy.agent_avatar_url || had.agent_avatar_url
  if (name === had.agent_name && avatar === had.agent_avatar_url) return had
  return { ...had, agent_name: name, agent_avatar_url: avatar }
}
