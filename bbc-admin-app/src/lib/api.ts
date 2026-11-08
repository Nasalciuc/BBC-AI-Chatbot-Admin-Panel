/**
 * Centralised API client – the ONLY file that talks to the FastAPI backend.
 * Every request injects HTTP Basic-Auth via VITE_API_USER / VITE_API_PASS.
 * On network failure the callers fall back to mock data in their own components.
 */

/** Structured API error with HTTP status code for QueryCache error handling. */
export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

import type {
  AccessEvent,
  Conversation,
  ConversationsResponse,
  DashboardStats,
  EarlierLead,
  Handover,
  LeadsOrder,
  LeadsView,
  LeadsViewResponse,
  PersonDay,
  Reading,
  TeamNow,
  ToReadList,
  OwnerDashboard,
  PanelUser,
  KBCategory,
  KBEntry,
  KBEntryCreate,
  Lead,
  LeadsResponse,
  Message,
  NotificationsResponse,
  TaskKind,
  TasksViewResponse,
  TaskDetail,
  NewTaskBody,
  TaskPatchBody,
  TaskRules,
  TaskTry,
  UserAccessAuditItem,
  AgentSseEvent,
  AgentDay,
  MyAccess,
} from './types'
import { parseAgentSseEvent } from './types'
import type { Team } from './bbc/types'
import { getCookie } from './cookies'
import { openSse, type SseHandle } from '@/lib/sse-reader'

// ── Config ────────────────────────────────────────────────────
const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
const USER = import.meta.env.VITE_API_USER ?? ''
const PASS = import.meta.env.VITE_API_PASS ?? ''

function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = {}
  // Prefer JWT Bearer token from cookie
  const cookie = getCookie('bbc_admin_token')
  if (cookie) {
    try {
      const token = JSON.parse(cookie)
      if (token) {
        headers['Authorization'] = `Bearer ${token}`
        return headers
      }
    } catch { /* fall through to Basic */ }
  }
  // Fallback to Basic Auth for dev/seed scenarios
  if (USER && PASS) {
    headers['Authorization'] = `Basic ${btoa(`${USER}:${PASS}`)}`
  }
  return headers
}

// ── Health ────────────────────────────────────────────────────
/** Whether the server answers /health (the status band, the maintenance
 *  page). Never throws: no answer in time is "not reachable". */
export async function checkHealth(timeoutMs = 8000): Promise<boolean> {
  try {
    const res = await fetch(`${BASE}/health`, { signal: AbortSignal.timeout(timeoutMs) })
    return res.ok
  } catch {
    return false
  }
}

// ── Auth ──────────────────────────────────────────────────────
export async function loginUser(email: string, password: string) {
  const res = await fetch(`${BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
    signal: AbortSignal.timeout(8000),
  })
  if (!res.ok) {
    // The status decides what the sign-in page says (wrong, off, too many tries).
    const body = await res.json().catch(() => ({ detail: 'Login failed' }))
    throw new ApiError(res.status, typeof body?.detail === 'string' ? body.detail : 'Login failed')
  }
  return res.json()
}

/** Set the password from an invite link. 400 carries why the link failed. */
export async function setPasswordFromInvite(token: string, password: string): Promise<void> {
  await apiFetch('/api/auth/set-password', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token, password }),
  })
}

/** Ask for a new invite link. The answer is the same whether or not the
 *  address has an account. */
export async function requestInviteLink(email: string): Promise<void> {
  await apiFetch('/api/auth/request-invite', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  })
}

/** The panel's live channel for one conversation. Auth comes from the same
 *  authHeaders() every request uses — components never see a token. EventSource
 *  cannot send headers and a session JWT in a query string lands in every proxy
 *  log, hence fetch+ReadableStream under the hood. Frames are validated here;
 *  consumers receive AgentSseEvent, never unknown. */
export function openAgentStream(
  conversationId: string,
  onEvent: (e: AgentSseEvent) => void,
  onStateChange: (state: 'open' | 'reconnecting' | 'closed') => void,
  onGiveUp: () => void,
): SseHandle {
  return openSse(
    `${BASE}/api/agent/stream/${conversationId}`,
    authHeaders(),
    (raw) => { const e = parseAgentSseEvent(raw); if (e) onEvent(e) },
    onStateChange,
    onGiveUp,
  )
}

// ── Generic fetch wrapper ─────────────────────────────────────
export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
  timeoutMs = 8000,
): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...authHeaders(),
      ...(init.headers as Record<string, string> | undefined),
    },
    signal: AbortSignal.timeout(timeoutMs),
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    // FastAPI answers {"detail": "..."} — throwing the raw body meant
    // every error toast (and the set-password page) showed JSON to a
    // human. Unwrap it once, here, for every caller.
    let message = text || `HTTP ${res.status}`
    try {
      const body = JSON.parse(text) as { detail?: unknown }
      if (typeof body?.detail === 'string' && body.detail) message = body.detail
    } catch {
      /* not JSON — keep the raw text */
    }
    throw new ApiError(res.status, message)
  }
  // 204 No Content → return undefined
  if (res.status === 204) return undefined as unknown as T
  return res.json() as Promise<T>
}

// ── Dashboard ─────────────────────────────────────────────────
/** Your day: the caller's own chats. Days cut at the browser's local midnight. */
export async function getAgentDay(days: 1 | 7 | 30): Promise<AgentDay> {
  const offset = -new Date().getTimezoneOffset()
  const res = await apiFetch<{ success: boolean; data: AgentDay }>(
    `/api/agent/day?days=${days}&tz_offset_minutes=${offset}`
  )
  return res.data
}

/** The owner's Dashboard (owner/admin/dev). Days and hours are the browser's. */
export async function getOwnerDashboard(days: 1 | 7 | 30): Promise<OwnerDashboard> {
  const offset = -new Date().getTimezoneOffset()
  const res = await apiFetch<{ success: boolean; data: OwnerDashboard }>(
    `/api/dashboard/owner?days=${days}&tz_offset_minutes=${offset}`,
    {},
    20_000,
  )
  return res.data
}

/** Supervisor, PM and QA: the same dashboard cut to their role (their
 *  tunnel, no AI cost, no sources; supervisors' names masked). */
export async function getTeamDashboard(days: 1 | 7 | 30): Promise<OwnerDashboard> {
  const offset = -new Date().getTimezoneOffset()
  const res = await apiFetch<{ success: boolean; data: OwnerDashboard }>(
    `/api/dashboard/team?days=${days}&tz_offset_minutes=${offset}`,
    {},
    20_000,
  )
  return res.data
}

/** To read: this week's lost chats and last week's sample. */
export async function getToRead(): Promise<ToReadList> {
  const offset = -new Date().getTimezoneOffset()
  const res = await apiFetch<{ success: boolean; data: ToReadList }>(
    `/api/conversations/to-read?tz_offset_minutes=${offset}`,
    {},
    20_000,
  )
  return res.data
}

/** One chat for reading: messages with senders, what happened, the rules it broke. */
export async function getReading(conversationId: string): Promise<Reading> {
  const offset = -new Date().getTimezoneOffset()
  const res = await apiFetch<{ success: boolean; data: Reading }>(
    `/api/conversations/${encodeURIComponent(conversationId)}/reading?tz_offset_minutes=${offset}`,
    {},
    20_000,
  )
  return res.data
}

/** Team · Now: the floor right now. */
export async function getTeamNow(): Promise<TeamNow> {
  const offset = -new Date().getTimezoneOffset()
  const res = await apiFetch<{ success: boolean; data: TeamNow }>(`/api/admin/team/now?tz_offset_minutes=${offset}`, {}, 20_000)
  return res.data
}

/** One person's day, for the panel on Team. */
export async function getPersonDay(userId: string): Promise<PersonDay> {
  const offset = -new Date().getTimezoneOffset()
  const res = await apiFetch<{ success: boolean; data: PersonDay }>(
    `/api/admin/team/people/${encodeURIComponent(userId)}/today?tz_offset_minutes=${offset}`,
    {},
    20_000,
  )
  return res.data
}

export function getDashboardStats(): Promise<DashboardStats> {
  return apiFetch<DashboardStats>('/api/dashboard/stats')
}

// ── Conversations ─────────────────────────────────────────────
export function getConversations(
  params: Record<string, string> = {},
): Promise<ConversationsResponse> {
  const qs = new URLSearchParams(params).toString()
  return apiFetch<ConversationsResponse>(`/api/conversations?${qs}`)
}

/**
 * A conversation that genuinely no longer exists resolves to null — the caller
 * renders "not found" for that, which is true. Every other failure keeps
 * throwing so React Query's retry runs and the UI offers a retry instead of
 * claiming the conversation was deleted.
 */
export async function getConversation(id: string): Promise<Conversation | null> {
  try {
    const res = await apiFetch<{ success: boolean; data: Conversation }>(`/api/conversations/${encodeURIComponent(id)}`)
    return res.data ?? null
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null
    throw err
  }
}

/** Claim from the shared queue — the DATABASE decides; 409 names the winner. */
/** PUSH the lead to the CRM (the server runs the gate and the real submit).
 *  20 s: the CRM call alone can take 10 s — the default 8 s abort would
 *  "fail" a push that actually succeeded (double-push bait). */
export function pushLeadToCrm(leadId: string): Promise<{ success: boolean; data: Lead }> {
  return apiFetch(`/api/leads/${encodeURIComponent(leadId)}/mark-crm-created`, { method: 'PATCH' }, 20_000)
}

/** The operator added the client in the CRM by hand: record the CRM's lead
 *  number as the proof. No push happens. */
export function setLeadCrmId(leadId: string, crmId: string): Promise<{ success: boolean; data: Lead }> {
  return apiFetch(
    `/api/leads/${encodeURIComponent(leadId)}/crm-id?crm_id=${encodeURIComponent(crmId)}`,
    { method: 'PATCH' }
  )
}

export function claimConversation(id: string): Promise<{ success: boolean }> {
  return apiFetch(`/api/conversations/${encodeURIComponent(id)}/claim`, { method: 'POST' })
}

export async function getOperatorHistory(conversationId: string) {
  return apiFetch<{ source: string; events: Array<{ action: string; agent_name?: string; happened_at: string; handoff_reason?: string; response_seconds?: number }>; summary: string }>(
    `/api/conversations/${encodeURIComponent(conversationId)}/operator-history`
  )
}

export async function updateConversation(
  id: string,
  data: Partial<Conversation>,
): Promise<Conversation> {
  const res = await apiFetch<{ success: boolean; data: Conversation }>(`/api/conversations/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return res.data
}

export async function sendAgentMessage(
  conversationId: string,
  content: string,
): Promise<Message> {
  const res = await apiFetch<{ success: boolean; data: Message }>(
    `/api/conversations/${encodeURIComponent(conversationId)}/messages`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    },
  )
  if (!res.success) throw new ApiError(500, 'Failed to send message')
  return res.data
}

/** Report that the operator is typing — the widget shows "<name> is typing…". */
export function postAgentTyping(conversationId: string, text: string): Promise<{ success: boolean }> {
  return apiFetch(`/api/conversations/${encodeURIComponent(conversationId)}/agent-typing`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text }),
  })
}

export function clearAgentTyping(conversationId: string): Promise<{ success: boolean }> {
  return apiFetch(`/api/conversations/${encodeURIComponent(conversationId)}/agent-typing`, {
    method: 'DELETE',
  })
}

export async function reassignConversation(
  conversationId: string,
  agentId: string,
): Promise<{ success: boolean }> {
  return apiFetch(`/api/conversations/${encodeURIComponent(conversationId)}/reassign`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agent_id: agentId }),
  })
}

// ── Blocklist (abuse) ─────────────────────────────────────────
// Blocks the visitor's phone + email and records their IP. Only phone/email
// refuse future visitors — a shared IP never blocks on its own (backend policy).
export async function blockConversationVisitor(
  conversationId: string,
  reason?: string,
): Promise<{ success: boolean; blocked: string[] }> {
  return apiFetch(`/api/conversations/${encodeURIComponent(conversationId)}/block`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason: reason ?? null }),
  })
}

export async function unblockConversationVisitor(
  conversationId: string,
): Promise<{ success: boolean; unblocked: string[] }> {
  return apiFetch(`/api/conversations/${encodeURIComponent(conversationId)}/unblock`, {
    method: 'POST',
  })
}

// ── Leads ─────────────────────────────────────────────────────
export function getLeads(
  params: Record<string, string> = {},
): Promise<LeadsResponse> {
  const qs = new URLSearchParams(params).toString()
  return apiFetch<LeadsResponse>(`/api/leads?${qs}`)
}

/** The Leads page: one tab, its filters, and every tab's count. */
export function getLeadsView(params: {
  view: LeadsView
  search?: string
  level?: string
  chat_with?: string
  order?: LeadsOrder
  limit?: number
  offset?: number
}): Promise<LeadsViewResponse> {
  // "Reviewed by you today" is the viewer's own day.
  const qs = new URLSearchParams({ tz_offset_minutes: String(-new Date().getTimezoneOffset()) })
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '' && v !== null) qs.set(k, String(v))
  }
  return apiFetch<LeadsViewResponse>(`/api/leads?${qs.toString()}`, {}, 20_000)
}

/** Other chats with the same phone or e-mail. */
export async function getEarlierLeads(leadId: string): Promise<EarlierLead[]> {
  const res = await apiFetch<{ success: boolean; data: EarlierLead[] }>(
    `/api/leads/${encodeURIComponent(leadId)}/earlier`
  )
  return res.data ?? []
}

export async function getLeadFull(id: string): Promise<Lead> {
  const res = await apiFetch<Lead & { success?: boolean; data?: Lead }>(`/api/leads/${encodeURIComponent(id)}`)
  if (res && typeof res === 'object' && 'data' in res && res.data) return res.data
  return res as Lead
}

export async function updateLeadStatus(
  id: string,
  status: string,
): Promise<Lead> {
  const res = await apiFetch<Lead & { success?: boolean; data?: Lead }>(`/api/leads/${encodeURIComponent(id)}/status`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  })
  if (res && typeof res === 'object' && 'data' in res && res.data) return res.data
  return res as Lead
}

export async function reviewLead(
  leadId: string,
  reviewed: boolean,
  qaNotes?: string,
) {
  return apiFetch<{ success: boolean; reviewed: boolean }>(`/api/leads/${encodeURIComponent(leadId)}/review`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reviewed, qa_notes: qaNotes }),
  })
}

export async function updateLead(
  id: string,
  data: Partial<Lead>,
): Promise<Lead> {
  const res = await apiFetch<Lead & { success?: boolean; data?: Lead }>(`/api/leads/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (res && typeof res === 'object' && 'data' in res && res.data) return res.data
  return res as Lead
}

// ── Knowledge Base ────────────────────────────────────────────
/** The KB routes answer 200 with `success: false` when the database read
 *  failed. Reject then, so a page can tell an outage from an empty list. */
function kbRead<T>(res: { success?: boolean; data: T; error?: string }): { data: T } {
  if (res.success === false) throw new ApiError(503, res.error || 'Couldn’t read the knowledge base')
  return res
}

export async function getKBCategories(): Promise<{ data: KBCategory[] }> {
  return kbRead(await apiFetch<{ success?: boolean; data: KBCategory[]; error?: string }>('/api/kb/categories'))
}

export async function getKBEntries(
  params: Record<string, string> = {},
): Promise<{ data: KBEntry[] }> {
  const qs = new URLSearchParams(params).toString()
  return kbRead(await apiFetch<{ success?: boolean; data: KBEntry[]; error?: string }>(`/api/kb/entries?${qs}`))
}

export async function createKBEntry(data: KBEntryCreate): Promise<KBEntry> {
  const res = await apiFetch<{ success: boolean; data: KBEntry }>('/api/kb/entries', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return res.data
}

export async function updateKBEntry(
  id: string,
  data: Partial<KBEntry>,
): Promise<KBEntry> {
  const res = await apiFetch<{ success: boolean; data: KBEntry }>(`/api/kb/entries/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return res.data
}

export function deleteKBEntry(id: string): Promise<void> {
  return apiFetch<void>(`/api/kb/entries/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
}

// ── Users ─────────────────────────────────────────────────────
export function getUsers(
  params: Record<string, string> = {},
): Promise<{ success: boolean; data: Record<string, unknown>[]; count: number }> {
  const qs = new URLSearchParams({ limit: '100', ...params }).toString()
  return apiFetch(`/api/admin/users?${qs}`)
}

/** Users & access: everyone, with how they sign in (owner/admin/dev). */
export async function getPanelUsers(): Promise<PanelUser[]> {
  const res = await apiFetch<{ success: boolean; data: PanelUser[] }>('/api/admin/users?limit=200')
  return res.data ?? []
}

export async function getAccessLog(limit = 200): Promise<AccessEvent[]> {
  const res = await apiFetch<{ success: boolean; data: AccessEvent[] }>(`/api/admin/access-log?limit=${limit}`)
  return res.data ?? []
}

/** What deactivating someone touches: open chats, returning clients, leads. */
export async function getUserHandover(id: string): Promise<Handover> {
  const res = await apiFetch<{ success: boolean; data: Handover }>(
    `/api/admin/users/${encodeURIComponent(id)}/handover`
  )
  return res.data
}

export async function getLiveAgents(): Promise<import('./types').LiveAgent[]> {
  const res = await apiFetch<{
    success: boolean
    data: import('./types').LiveAgent[]
    count: number
  }>('/api/admin/agents/live')
  return res.data ?? []
}

export async function inviteUser(data: {
  name: string
  email: string
  role: string
  tunnel_scope: string
  phone?: string
}): Promise<{ success: boolean; data: Record<string, unknown> }> {
  return apiFetch('/api/auth/invite', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export async function updateUser(
  id: string,
  data: { name?: string; role?: string; is_active?: boolean; chat_enabled?: boolean; tunnel_scope?: string; phone?: string; avatar_url?: string | null },
): Promise<{ success: boolean; data: Record<string, unknown> }> {
  return apiFetch(`/api/admin/users/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export interface SelfUpdatePayload {
  name?: string
  phone?: string
  avatar_url?: string
}

export interface SelfUpdateResponse {
  user: {
    id: string
    email: string
    name?: string
    phone?: string
    avatar_url?: string | null
    role?: string
    tunnel_scope?: string
  }
  token: string
}

export async function updateSelf(payload: SelfUpdatePayload): Promise<SelfUpdateResponse> {
  return apiFetch<SelfUpdateResponse>('/api/auth/me', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

/** Settings › Profile: your access as the database has it now (the
 *  session keeps the role it signed in with until it expires). */
export async function getMyAccess(): Promise<MyAccess> {
  const res = await apiFetch<{ success: boolean; data: MyAccess }>('/api/auth/me')
  return res.data
}

/** Upload a profile photo (multipart). Do NOT set Content-Type — browser sets boundary. */
export async function uploadAvatar(
  file: File,
): Promise<{ avatar_url: string; token: string }> {
  const fd = new FormData()
  fd.append('file', file)
  // Longer timeout — image upload can exceed the default 8s.
  return apiFetch('/api/auth/me/avatar', { method: 'POST', body: fd }, 30_000)
}

export async function deactivateUser(id: string): Promise<{ success: boolean; data: Record<string, unknown> }> {
  return updateUser(id, { is_active: false })
}

// ── Teams (Phase 3 UI — consumes Phase 1 CRUD endpoints) ──────
export interface TeamCreatePayload {
  name: string
  shift_name?: string | null
  shift_start?: string | null
  shift_end?: string | null
  supervisor_id?: string | null
  pm_id?: string | null
}

export type TeamUpdatePayload = Partial<TeamCreatePayload> & { is_active?: boolean }

export async function getTeams(params: { is_active?: boolean } = {}): Promise<Team[]> {
  const qs = new URLSearchParams()
  if (params.is_active !== undefined) qs.set('is_active', String(params.is_active))
  const q = qs.toString()
  const res = await apiFetch<{ success: boolean; data: Team[]; count: number }>(
    `/api/admin/teams${q ? `?${q}` : ''}`,
  )
  return res.data ?? []
}

export async function createTeam(body: TeamCreatePayload): Promise<Team> {
  const res = await apiFetch<{ success: boolean; data: Team }>('/api/admin/teams', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return res.data
}

export async function updateTeam(id: string, body: TeamUpdatePayload): Promise<Team> {
  const res = await apiFetch<{ success: boolean; data: Team }>(
    `/api/admin/teams/${encodeURIComponent(id)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    },
  )
  return res.data
}

export async function deleteTeam(id: string): Promise<void> {
  await apiFetch(`/api/admin/teams/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

/** Assign (teamId) or unassign (null) an operator's team. */
export async function assignUserToTeam(userId: string, teamId: string | null): Promise<void> {
  await apiFetch(`/api/admin/users/${encodeURIComponent(userId)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ team_id: teamId }),
  })
}

export function getUserAccessHistory(
  id: string,
  limit = 30,
): Promise<{ success: boolean; data: UserAccessAuditItem[]; count: number }> {
  return apiFetch(`/api/admin/users/${encodeURIComponent(id)}/access-history?limit=${limit}`)
}

// ── Chat (used by widget / playground) ────────────────────────
export interface ChatResponse {
  conversation_id: string
  message: string
  type: string       // "template" | "ai" | "template_fallback"
  model_used: string
  cost: number
  route_card?: {
    origin: string
    destination: string
    airlines?: string
    duration?: string
    price_range?: string
  } | null
}

export function sendChatMessage(
  conversationId: string | null,
  message: string,
  tunnel: 'sales' | 'support' = 'sales',
): Promise<ChatResponse> {
  return apiFetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ conversation_id: conversationId, message, tunnel }),
  })
}

// ── Notifications ────────────────────────────────────────
export function getNotifications() {
  return apiFetch<NotificationsResponse>('/api/notifications')
}

// ── Tasks: follow-ups ───────────────────────────────────
export function getTasksView(params: { view?: string; kind?: TaskKind | null; conversationId?: string } = {}) {
  const qs = new URLSearchParams()
  if (params.view) qs.set('view', params.view)
  if (params.kind) qs.set('kind', params.kind)
  if (params.conversationId) qs.set('conversation_id', params.conversationId)
  const q = qs.toString()
  return apiFetch<TasksViewResponse>(`/api/tasks${q ? `?${q}` : ''}`)
}

export function getTaskDetail(id: string) {
  return apiFetch<{ success: boolean; data: TaskDetail }>(`/api/tasks/${encodeURIComponent(id)}`)
}

export function createTask(body: NewTaskBody) {
  return apiFetch<{ success: boolean; data: { id: string } }>('/api/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function updateTask(id: string, body: TaskPatchBody) {
  return apiFetch<{ success: boolean; data: { id: string } }>(`/api/tasks/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function deleteTask(id: string) {
  return apiFetch<{ success: boolean }>(`/api/tasks/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export function getTaskRules() {
  return apiFetch<{ success: boolean; data: TaskRules }>('/api/tasks/rules')
}

export function saveTaskRules(patch: Partial<TaskRules>) {
  return apiFetch<{ success: boolean; data: TaskRules }>('/api/tasks/rules', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  })
}

export function tryTaskSentence(sentence: string) {
  return apiFetch<{ success: boolean; data: TaskTry }>('/api/tasks/rules/try', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sentence }),
  })
}
