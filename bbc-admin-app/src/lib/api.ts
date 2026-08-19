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
  Conversation,
  ConversationsResponse,
  DashboardStats,
  KBCategory,
  KBEntry,
  KBEntryCreate,
  Lead,
  LeadsResponse,
  Message,
  NotificationsResponse,
  Task,
  TasksResponse,
  UserAccessAuditItem,
} from './types'
import type { Team } from './bbc/types'
import { getCookie } from './cookies'

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

// ── Auth ──────────────────────────────────────────────────────
export async function loginUser(email: string, password: string) {
  const res = await fetch(`${BASE}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
    signal: AbortSignal.timeout(8000),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: 'Login failed' }))
    throw new Error(body.detail || 'Login failed')
  }
  return res.json()
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
export function getKBCategories(): Promise<{ data: KBCategory[] }> {
  return apiFetch<{ data: KBCategory[] }>('/api/kb/categories')
}

export function getKBEntries(
  params: Record<string, string> = {},
): Promise<{ data: KBEntry[] }> {
  const qs = new URLSearchParams(params).toString()
  return apiFetch<{ data: KBEntry[] }>(`/api/kb/entries?${qs}`)
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

// ── Tasks ──────────────────────────────────────────────
export function getTasks() {
  return apiFetch<TasksResponse>('/api/tasks')
}

export function createTask(data: {
  title: string
  description?: string
  status: string
  label: string
  priority: string
  assignee_id?: string
  due_date?: string
}) {
  return apiFetch<{ success: boolean; data: Task }>('/api/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export function updateTask(id: string, data: Partial<{
  title: string
  description: string
  status: string
  label: string
  priority: string
  assignee_id: string
  due_date: string
}>) {
  return apiFetch<{ success: boolean; data: Task }>(`/api/tasks/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export function deleteTask(id: string) {
  return apiFetch<{ success: boolean }>(`/api/tasks/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
}
