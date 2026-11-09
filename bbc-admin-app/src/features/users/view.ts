// What Users & access says — pure, pinned by tests/users-view.test.ts.
import type { AccessEvent, PanelUser } from '../../lib/types.ts'
import type { AvatarTone } from '../../components/person-avatar.tsx'

export const ROLE_ORDER = ['sales', 'support', 'supervisor', 'project_manager', 'qa', 'admin', 'dev', 'owner'] as const
export type Role = (typeof ROLE_ORDER)[number]

export const ROLE_LABEL: Record<string, string> = {
  sales: 'Sales agent',
  support: 'Support agent',
  supervisor: 'Supervisor',
  project_manager: 'Project manager',
  qa: 'QA',
  admin: 'Admin',
  dev: 'Developer',
  owner: 'Owner',
}

export const ROLE_SHORT: Record<string, string> = { ...ROLE_LABEL, sales: 'Sales', support: 'Support' }

export const ROLE_TEXT: Record<Role, string> = {
  sales: 'Answers sales chats. Sees their own chats and the sales queue.',
  support: 'The same, for support chats about existing bookings.',
  supervisor: 'Leads one team: reads its chats live, gives chats to others, blocks visitors.',
  project_manager: 'Builds teams and moves people between them. Reads chats, doesn’t reply.',
  qa: 'Reads every chat and reviews leads. Doesn’t reply.',
  admin: 'Everything except changing owners.',
  dev: 'Everything an admin can, except leads.',
  owner: 'Everything, including other owners. Keep this to one or two people.',
}

export function tunnelFor(role: string): 'sales' | 'support' | 'all' {
  return role === 'sales' ? 'sales' : role === 'support' ? 'support' : 'all'
}

export const TUNNEL_LABEL: Record<string, string> = { sales: 'Sales', support: 'Support', all: 'All' }
export const TUNNEL_LONG: Record<string, string> = { sales: 'Sales chats only', support: 'Support chats only', all: 'All chats' }

// ── Status ────────────────────────────────────────────────────────────

export type StatusKey = 'active' | 'pending' | 'expired' | 'never' | 'deactivated'

export interface StatusView {
  key: StatusKey
  label: string
  sub: string
  chip: string
  subCls: string
}

function hoursText(ms: number): string {
  const h = Math.round(Math.abs(ms) / 3_600_000)
  if (h < 1) return `${Math.max(1, Math.round(Math.abs(ms) / 60_000))} min`
  if (h < 48) return `${h} h`
  return `${Math.round(h / 24)} days`
}

export function statusOf(u: PanelUser, nowMs: number): StatusView {
  if (!u.is_active) return { key: 'deactivated', label: 'Deactivated', sub: '', chip: 'bg-k-f0f0f0 text-k-555555', subCls: '' }
  if (u.last_seen_at) return { key: 'active', label: 'Active', sub: '', chip: 'bg-k-e3f6ea text-k-0a6b35', subCls: '' }
  const inv = u.invite
  const exp = inv?.expires_at ? new Date(inv.expires_at).getTime() : NaN
  if (inv && !inv.used_at && !Number.isNaN(exp) && exp > nowMs) {
    return { key: 'pending', label: 'Invite pending', sub: `Expires in ${hoursText(exp - nowMs)}`, chip: 'bg-k-fff3e0 text-k-8a4b00', subCls: 'text-k-555555' }
  }
  if (inv && !inv.used_at && !Number.isNaN(exp)) {
    return { key: 'expired', label: 'Invite expired', sub: `Expired ${hoursText(nowMs - exp)} ago`, chip: 'bg-k-fdecea text-k-b3261e', subCls: 'text-k-b3261e' }
  }
  // No invite, or one already used (the password is set) with no sign-in since.
  return { key: 'never', label: 'Not signed in yet', sub: '', chip: 'bg-k-f0f0f0 text-k-555555', subCls: '' }
}

export function isInvite(s: StatusKey): boolean {
  return s === 'pending' || s === 'expired' || s === 'never'
}

/** "Now", "20 min ago", "Today 11:20", "Yesterday", "12 Oct", "Never signed in". */
export function lastActive(iso: string | null | undefined, nowMs: number, timeZone?: string): string {
  if (!iso) return 'Never signed in'
  const t = new Date(iso)
  const ms = nowMs - t.getTime()
  if (Number.isNaN(ms)) return ''
  if (ms < 10 * 60_000) return 'Now'
  if (ms < 60 * 60_000) return `${Math.floor(ms / 60_000)} min ago`
  const opts = timeZone ? { timeZone } : {}
  const day = (d: Date) => d.toLocaleDateString('en-CA', opts)
  const now = new Date(nowMs)
  if (day(t) === day(now)) return `Today ${t.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', ...opts })}`
  if (day(t) === day(new Date(nowMs - 86_400_000))) return 'Yesterday'
  return t.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', ...opts })
}

/** The access log's time: "Just now", "Today 13:52", "Yesterday 18:20", "2 Nov, 10:15". */
export function logWhen(iso: string, nowMs: number, timeZone?: string): string {
  const t = new Date(iso)
  const ms = nowMs - t.getTime()
  if (Number.isNaN(ms)) return ''
  if (ms < 5 * 60_000) return 'Just now'
  const opts = timeZone ? { timeZone } : {}
  const time = t.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', ...opts })
  const day = (d: Date) => d.toLocaleDateString('en-CA', opts)
  if (day(t) === day(new Date(nowMs))) return `Today ${time}`
  if (day(t) === day(new Date(nowMs - 86_400_000))) return `Yesterday ${time}`
  return `${t.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', ...opts })}, ${time}`
}

export function avatarTone(u: PanelUser, s: StatusKey): AvatarTone {
  if (s === 'deactivated') return 'gray'
  if (isInvite(s)) return 'amber'
  if (u.role === 'sales') return 'green'
  if (u.role === 'support') return 'blue'
  return 'gray'
}

// ── What each role can do (from the endpoints, Nov 2026) ──────────────

/** 'y' = yes, 'n' = no, anything else is the scope in words. */
export type Cell = 'y' | 'n' | string

export interface Capability {
  label: string
  sub?: string
  cells: Record<Role, Cell>
}

const row = (label: string, cells: Cell[], sub?: string): Capability => ({
  label,
  sub,
  cells: Object.fromEntries(ROLE_ORDER.map((r, i) => [r, cells[i]])) as Record<Role, Cell>,
})

//                                     sales           support         supervisor    PM            QA      admin         dev           owner
export const CAPABILITIES: Capability[] = [
  row('Answer chats', ['Sales', 'Support', 'n', 'n', 'n', 'y', 'y', 'y'], 'Take from the queue and reply'),
  row('See chats', ['Own + queue', 'Own + queue', 'Their team', 'Their teams', 'All', 'All', 'All', 'All']),
  row('Give a chat to someone else', ['n', 'n', 'y', 'n', 'n', 'y', 'y', 'y']),
  row('Block a visitor', ['n', 'n', 'y', 'y', 'n', 'y', 'y', 'y']),
  row('See a visitor’s phone and e-mail', ['y', 'y', 'n', 'y', 'y', 'y', 'y', 'y']),
  row('See leads', ['n', 'n', 'Their team', 'n', 'y', 'y', 'n', 'y']),
  row('Send a lead to the CRM', ['Their chats', 'n', 'n', 'n', 'n', 'y', 'y', 'y']),
  row('Review leads', ['n', 'n', 'Their team', 'n', 'y', 'y', 'n', 'y'], 'QA check'),
  row('Knowledge base', ['Read', 'Read', 'n', 'n', 'Read', 'Edit', 'Edit', 'Edit']),
  row('Team: live view and shifts', ['n', 'n', 'Their team', 'Their teams', 'n', 'y', 'y', 'y']),
  row('Build teams and move people', ['n', 'n', 'n', 'Their teams', 'n', 'y', 'y', 'y']),
  row('Invite people and change roles', ['n', 'n', 'n', 'n', 'n', 'Not owners', 'Not owners', 'y']),
  row('See AI cost and model names', ['n', 'n', 'n', 'n', 'n', 'y', 'y', 'y']),
  row('Settings', ['Own profile', 'Own profile', 'Own profile', 'Own profile', 'Own profile', 'All', 'All', 'All']),
]

export const ROLES_NOTE =
  'Each person has one role. The role decides what they see and what they can do; nobody can change their own role. A role change applies when they next sign in; a session that’s already open keeps the old role until it ends, at most 24 hours.'

export function canDo(role: string): { label: string; scope: string }[] {
  if (!(ROLE_ORDER as readonly string[]).includes(role)) return []
  return CAPABILITIES.filter((c) => c.cells[role as Role] !== 'n').map((c) => ({
    label: c.label,
    scope: c.cells[role as Role] === 'y' ? '' : c.cells[role as Role],
  }))
}

export function cannotDo(role: string): string[] {
  if (!(ROLE_ORDER as readonly string[]).includes(role)) return []
  return CAPABILITIES.filter((c) => c.cells[role as Role] === 'n').map((c) => c.label.charAt(0).toLowerCase() + c.label.slice(1))
}

/** What changes with a new role: what they gain and what they lose. */
export function roleChange(from: string, to: string): { gains: string[]; losses: string[] } {
  const a = new Map(canDo(from).map((c) => [c.label, c.scope]))
  const b = new Map(canDo(to).map((c) => [c.label, c.scope]))
  const gains: string[] = []
  const losses: string[] = []
  for (const [k, v] of b) if (!a.has(k)) gains.push(v ? `${k} (${v.toLowerCase()})` : k)
  for (const [k] of a) if (!b.has(k)) losses.push(k)
  return { gains, losses }
}

// ── Invites ───────────────────────────────────────────────────────────

const DOMAIN_FIXES: Record<string, string> = {
  'buybusinesclass.com': 'buybusinessclass.com',
  'buybussinessclass.com': 'buybusinessclass.com',
  'buybusinessclas.com': 'buybusinessclass.com',
  'buybusinessclass.co': 'buybusinessclass.com',
  'businessclass-ticket.com': 'businessclass-tickets.com',
  'businesclass-tickets.com': 'businessclass-tickets.com',
  'gmial.com': 'gmail.com',
  'gmai.com': 'gmail.com',
  'gmal.com': 'gmail.com',
  'gamil.com': 'gmail.com',
  'gmail.co': 'gmail.com',
  'hotmial.com': 'hotmail.com',
  'outlok.com': 'outlook.com',
  'yahooo.com': 'yahoo.com',
}

/** A likely misspelled domain, and the address it probably meant. */
export function emailTypo(email: string): { domain: string; fixed: string; suggestion: string } | null {
  const m = email.trim().toLowerCase().match(/^([^@\s]+)@([^@\s]+)$/)
  if (!m) return null
  const fixed = DOMAIN_FIXES[m[2]]
  return fixed ? { domain: m[2], fixed, suggestion: `${m[1]}@${fixed}` } : null
}

export function validEmail(email: string): boolean {
  return /^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(email.trim())
}

// ── Access log ────────────────────────────────────────────────────────

export type LogFilter = 'all' | 'roles' | 'invites' | 'status' | 'chats'

export function logFilterOf(e: AccessEvent): Exclude<LogFilter, 'all'> | 'other' {
  if (e.kind === 'role' || e.kind === 'tunnel') return 'roles'
  if (e.kind === 'invited' || e.kind === 'invite_again') return 'invites'
  if (e.kind === 'deactivated' || e.kind === 'reactivated') return 'status'
  if (e.kind === 'chats_on' || e.kind === 'chats_off') return 'chats'
  return 'other'
}

export function accessSentence(e: AccessEvent): { actor: string; text: string } {
  const who = e.target_name ?? 'someone'
  const actor = e.actor_name ?? 'Someone'
  const role = (r?: string | null) => (r ? ROLE_LABEL[r] ?? r : '')
  switch (e.kind) {
    case 'invited':
      return { actor, text: `invited ${who}${e.role ? ` as ${role(e.role)}` : ''}.` }
    case 'invite_again':
      return { actor, text: `sent ${who} a new invite.` }
    case 'role':
      return { actor, text: `changed ${who}’s role: ${role(e.old)} → ${role(e.new)}.` }
    case 'tunnel':
      return { actor, text: `changed ${who}’s chats: ${TUNNEL_LONG[e.old ?? ''] ?? e.old} → ${TUNNEL_LONG[e.new ?? ''] ?? e.new}.` }
    case 'deactivated':
      return { actor, text: `deactivated ${who}.` }
    case 'reactivated':
      return { actor, text: `turned ${who}’s account back on.` }
    case 'chats_on':
      return { actor, text: `turned on “Receives chats” for ${who}.` }
    case 'chats_off':
      return { actor, text: `turned off “Receives chats” for ${who}.` }
    case 'password_reset':
      return { actor: e.actor_name ?? 'An admin', text: `reset ${who}’s password by hand.` }
  }
}

export const LOG_DOT: Record<AccessEvent['kind'], string> = {
  invited: 'bg-k-d97a00',
  invite_again: 'bg-k-d97a00',
  role: 'bg-k-5b45e0',
  tunnel: 'bg-k-5b45e0',
  deactivated: 'bg-k-9c9c9c',
  reactivated: 'bg-k-00a645',
  chats_on: 'bg-k-00a645',
  chats_off: 'bg-k-d93025',
  password_reset: 'bg-kb-202020',
}
