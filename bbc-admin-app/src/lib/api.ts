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
} from './types'
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
    throw new ApiError(res.status, text || `HTTP ${res.status}`)
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

export async function getConversation(id: string): Promise<Conversation> {
  const res = await apiFetch<{ success: boolean; data: Conversation }>(`/api/conversations/${encodeURIComponent(id)}`)
  return res.data
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

// ── Leads ─────────────────────────────────────────────────────
export function getLeads(
  params: Record<string, string> = {},
): Promise<LeadsResponse> {
  const qs = new URLSearchParams(params).toString()
  return apiFetch<LeadsResponse>(`/api/leads?${qs}`)
}

export async function getLeadFull(id: string): Promise<Lead> {
  const res = await apiFetch<{ success: boolean; data: Lead }>(`/api/leads/${encodeURIComponent(id)}`)
  return res.data
}

export async function updateLeadStatus(
  id: string,
  status: string,
): Promise<Lead> {
  const res = await apiFetch<{ success: boolean; data: Lead }>(`/api/leads/${encodeURIComponent(id)}/status`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  })
  return res.data
}

export async function updateLead(
  id: string,
  data: Partial<Lead>,
): Promise<Lead> {
  const res = await apiFetch<{ success: boolean; data: Lead }>(`/api/leads/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  return res.data
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
): Promise<{ success: boolean; data: any[]; count: number }> {
  const qs = new URLSearchParams(params).toString()
  return apiFetch(`/api/admin/users?${qs}`)
}

export async function inviteUser(data: {
  name: string
  email: string
  role: string
  tunnel_scope: string
  password: string
  phone?: string
}): Promise<{ success: boolean; data: any }> {
  return apiFetch('/api/auth/invite', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export async function updateUser(
  id: string,
  data: { name?: string; role?: string; is_active?: boolean; tunnel_scope?: string; phone?: string },
): Promise<{ success: boolean; data: any }> {
  return apiFetch(`/api/admin/users/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
}

export async function deactivateUser(id: string): Promise<{ success: boolean; data: any }> {
  return updateUser(id, { is_active: false })
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
