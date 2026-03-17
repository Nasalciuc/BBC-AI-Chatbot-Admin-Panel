/**
 * Centralised API client – the ONLY file that talks to the FastAPI backend.
 * Every request injects HTTP Basic-Auth via VITE_API_USER / VITE_API_PASS.
 * On network failure the callers fall back to mock data in their own components.
 */

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

// ── Config ────────────────────────────────────────────────────
const BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
const USER = import.meta.env.VITE_API_USER ?? ''
const PASS = import.meta.env.VITE_API_PASS ?? ''

function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = {}
  if (USER && PASS) {
    headers['Authorization'] = `Basic ${btoa(`${USER}:${PASS}`)}`
  }
  return headers
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
    throw new Error(`API ${res.status}: ${text}`)
  }
  // 204 No Content → return undefined
  if (res.status === 204) return undefined as unknown as T
  return res.json() as Promise<T>
}

// ── Dashboard ─────────────────────────────────────────────────
export async function getDashboardStats(): Promise<DashboardStats> {
  const res = await apiFetch<{ success: boolean; data: DashboardStats }>('/api/dashboard/stats')
  return res.data
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
