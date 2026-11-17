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
 */
export function currentOperator(rows: readonly OperatorRow[]): Operator | null {
  for (let i = rows.length - 1; i >= 0; i--) {
    const m = rows[i]
    if (m.role === 'ai') return null
    if (m.role === 'agent') {
      return {
        name: (m.agent_name || '').trim() || CONSULTANT_LABEL,
        avatarUrl: m.agent_avatar_url || null,
      }
    }
  }
  return null
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
