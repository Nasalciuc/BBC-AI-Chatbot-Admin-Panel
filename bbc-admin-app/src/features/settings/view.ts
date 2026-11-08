// What Settings says — pure, pinned by tests/settings-view.test.ts.
import type { AccessTeam, MyAccess } from '../../lib/types.ts'
import { ROLE_LABEL, ROLE_TEXT, type Role } from '../users/view.ts'

export type SettingsSection = 'profile' | 'alerts' | 'security' | 'appearance'

// ── Profile ───────────────────────────────────────────────────────────

/** The server keeps 1–100 characters; two is the least that reads as a name. */
export function nameError(v: string): string {
  const n = v.trim()
  if (n.length < 2) return 'Type your name, at least 2 characters.'
  if (n.length > 100) return 'Keep it under 100 characters.'
  return ''
}

/** Digits with the country code; spaces, dashes and brackets are fine. */
export function phoneError(v: string): string {
  const d = v.replace(/[\s().-]/g, '')
  if (!d) return ''
  return /^\+?[1-9]\d{6,14}$/.test(d) ? '' : 'Use digits, with + and the country code, e.g. +373 69 123 456.'
}

export const PHOTO_TYPES = ['image/jpeg', 'image/png', 'image/webp']
export const PHOTO_MAX_BYTES = 2_000_000

/** Checked before the upload, with the server's own limits. */
export function photoError(file: { type: string; size: number }): string {
  if (!PHOTO_TYPES.includes(file.type)) return 'That file isn’t a JPG, PNG or WEBP picture.'
  if (file.size > PHOTO_MAX_BYTES) return `That photo is ${(file.size / 1_000_000).toFixed(1)} MB; the limit is 2 MB.`
  return ''
}

// ── Your access ───────────────────────────────────────────────────────

/** "12:00:00" → "12:00". */
function hhmm(t: string | null): string | null {
  return t ? t.slice(0, 5) : null
}

/** "Day shift · 12:00–20:00 · Victor Rusu (supervisor)". */
export function teamLine(t: AccessTeam, withSupervisor = true): string {
  const hours = t.shift_start && t.shift_end ? `${hhmm(t.shift_start)}–${hhmm(t.shift_end)}` : null
  const shift = t.shift_name && t.shift_name !== t.name ? t.shift_name : null
  const sup = withSupervisor && t.supervisor_name ? `${t.supervisor_name} (supervisor)` : null
  return [t.name ?? 'A team', shift, hours, sup].filter(Boolean).join(' · ')
}

const CHATS_TEXT: Record<string, string> = { sales: 'Sales only', support: 'Support only', all: 'Sales and support' }

export interface AccessRow {
  label: string
  value: string
  note?: string
}

/** Role, chats, team, and whether the queue can give you a chat. */
export function accessRows(a: MyAccess): AccessRow[] {
  const role = a.role
  const chats = CHATS_TEXT[a.tunnel_scope] ?? a.tunnel_scope
  const readsOnly = role === 'qa' || role === 'project_manager'
  let team: string
  if (role === 'supervisor' || role === 'project_manager') {
    team = a.leads_teams.length
      ? `Leads ${a.leads_teams.map((t) => teamLine(t, false)).join('; ')}`
      : role === 'supervisor'
        ? 'No team yet. An owner, admin or project manager gives you one.'
        : 'No teams yet'
  } else {
    team = a.team ? teamLine(a.team) : 'Not in a team'
  }
  // The queue and routing give chats to sales and support only
  // (_OPERATOR_ROLES); owner, admin and developer can still take one.
  const handsOn = role === 'owner' || role === 'admin' || role === 'dev'
  const receives = a.receives_chats
    ? 'Yes'
    : !a.is_active
      ? 'No: your account is turned off'
      : (role === 'sales' || role === 'support') && !a.chat_enabled
        ? 'No: an owner or admin switched your chats off'
        : handsOn
          ? 'No: the queue gives chats to agents. You can still take one yourself.'
          : 'No: your role reads chats and doesn’t answer them'
  return [
    { label: 'Role', value: ROLE_LABEL[role] ?? role, note: ROLE_TEXT[role as Role] },
    { label: 'Chats', value: readsOnly ? `${chats} · reads, never answers` : chats },
    { label: 'Team', value: team },
    { label: 'Receives chats', value: receives },
  ]
}

/** Who to ask when something in "Your access" is wrong. */
export function askLine(a: MyAccess): string {
  if (a.role === 'owner') return 'Something here is wrong? Ask another owner.'
  if (a.role === 'admin') return 'Something here is wrong? Ask an owner.'
  const sup = a.team?.supervisor_name
  return sup ? `Something here is wrong? Ask ${sup}, your supervisor, or an admin.` : 'Something here is wrong? Ask an owner or an admin.'
}

/** The session keeps the role it signed in with (24 h); say so when it changed. */
export function roleChangedNote(a: MyAccess): string | null {
  if (!a.session_role || a.session_role === a.role) return null
  const now = ROLE_LABEL[a.role] ?? a.role
  const before = ROLE_LABEL[a.session_role] ?? a.session_role
  return `Your role is now ${now}. This session still works as ${before} until you sign in again.`
}

// ── Security ──────────────────────────────────────────────────────────

const pad = (n: number) => String(n).padStart(2, '0')

/** "today at 12:02", "tomorrow at 12:02", or "on 9 Nov at 12:02" — local time. */
export function whenText(ms: number, nowMs: number): string {
  const d = new Date(ms)
  const n = new Date(nowMs)
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const diff = Math.round((day(d) - day(n)) / 86_400_000)
  const at = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  if (diff === 0) return `today at ${at}`
  if (diff === 1) return `tomorrow at ${at}`
  if (diff === -1) return `yesterday at ${at}`
  return `on ${d.getDate()} ${d.toLocaleString('en-GB', { month: 'short' })} at ${at}`
}

/** This session's end, from the token's expiry. */
export function sessionText(expMs: number | null | undefined, nowMs: number): string {
  const tail = 'A session lasts 24 hours, then you sign in again. Sessions on other devices can’t be ended from here yet.'
  if (!expMs || !Number.isFinite(expMs)) return tail
  if (expMs <= nowMs) return `This session has ended. ${tail}`
  return `This session ends ${whenText(expMs, nowMs)}. ${tail}`
}
