// ── Conversations ─────────────────────────────────────────────
export interface Message {
  id: string
  conversation_id: string
  role: 'user' | 'ai' | 'agent' | 'system'
  content: string
  model_used: string | null
  cost: number
  created_at: string
}

export type ConversationTag =
  | 'fresh'
  | 'active'
  | 'main_queue'
  | 'completed'
  | 'abandoned'
  | 'no_engagement'

export interface Conversation {
  id: string
  chat_number?: number | null
  tag?: ConversationTag | null
  request_id?: string | null
  tunnel: 'sales' | 'support'
  mode: 'ai' | 'human' | 'waiting_for_agent'
  status: 'active' | 'pending' | 'closed' | 'needs_agent'
  visitor_name: string | null
  visitor_email: string | null
  visitor_phone: string | null
  assigned_agent_id: string | null
  assigned_agent_name?: string | null
  engaged_agent_name?: string | null
  agent_state?: 'active' | 'fallback' | 'ai_only'
  message_count: number
  ai_cost_total: number
  created_at: string
  updated_at: string
  /** Activity clocks (migration 024). The list returns them whenever the
   *  supervisor columns are available; the Idle badge is derived from them.
   *  Optional because the backend drops them if 023/024 are not applied. */
  last_user_message_at?: string | null
  last_agent_message_at?: string | null
  closed_at: string | null
  summary?: string | null
  messages?: Message[]
  lead?: Lead | null
  metadata?: Record<string, unknown>
  has_flagged_content?: boolean
  flagged_reason?: string | null
}

export interface ConversationsResponse {
  success: boolean
  data: Conversation[]
  count: number
}

// ── Leads ─────────────────────────────────────────────────────
export interface Lead {
  id: string
  conversation_id: string
  trip_type: 'one_way' | 'round_trip' | 'multi_city' | 'open_jaw' | 'tour'
  cabin_class: 'business' | 'first' | 'economy' | 'mixed'
  passengers: number | null
  flexible_dates: boolean
  origin_code: string | null
  destination_code: string | null
  departure_date: string | null
  return_date: string | null
  route_display: string | null
  score: number
  tier: 'gold' | 'silver' | 'bronze'
  status: 'new' | 'contacted' | 'qualified' | 'converted' | 'lost'
  intent_signals: string[]
  notes: string
  created_at: string
  updated_at: string
  contacted_at: string | null
  converted_at: string | null
  created_in_crm?: boolean
  created_in_crm_at?: string | null
  /** The CRM's own id — the proof behind created_in_crm (migration 030). */
  crm_lead_id?: string | null
  crm_push_attempts?: number
  /** Why the quality gate refused the push (visible, never silent). */
  crm_push_gate_reason?: string | null
  visitor_name: string | null
  visitor_email: string | null
  visitor_phone: string | null
  reviewed_by_qa?: boolean
  reviewed_at?: string | null
  reviewed_by?: string | null
  qa_notes?: string | null
}

export interface LeadsResponse {
  success: boolean
  data: Lead[]
  count: number
  review_stats?: { reviewed: number; total: number }
}

// ── Knowledge Base ────────────────────────────────────────────
export interface KBCategory {
  id: string
  name: string
  tunnel: 'sales' | 'support'
  icon: string
  sort_order: number
  entry_count: number
}

export interface KBEntry {
  id: string
  category_id: string
  title: string
  content: string
  tunnel: 'sales' | 'support'
  is_active: boolean
  view_count: number
  created_at: string
  updated_at: string
}

export interface KBEntryCreate {
  category_id: string
  title: string
  content: string
  tunnel: 'sales' | 'support'
  is_active?: boolean
}

// ── Dashboard ─────────────────────────────────────────────────
export interface DashboardStats {
  conversations_today:  number
  conversations_week:   number
  conversations_month:  number
  conversations_active: number
  leads_total:     number
  leads_new:       number
  leads_contacted: number
  leads_qualified: number
  leads_converted: number
  leads_lost:      number
  leads_gold:   number
  leads_silver: number
  leads_bronze: number
  cost_today: number | null
  cost_week:  number | null
  cost_month: number | null
  top_routes:          Array<{ route: string; count: number }>
  conversations_trend: Array<{ date: string; count: number }>
  leads_trend:         Array<{ date: string; count: number }>

  // === V2 ADDITIONS ===
  conversations_yesterday: number
  leads_uncalled:          number
  leads_sla_breach:        number
  cost_avg_30d:            number | null
  daily_budget:            number | null
  latency_median_ms:       number | null
  fallback_rate_percent:   number | null
  cost_vs_budget_percent:  number | null
  avg_duration_minutes:    number
  messages_total_month:    number | null

  conversations_trend_v2: Array<{ date: string; sales: number; support: number }> | null

  hot_leads: Array<{
    id: string
    visitor_name: string | null
    route: string
    score: number
    tier: 'gold' | 'silver' | 'bronze'
    minutes_since_created: number
  }>

  leads_sparkline_7d: number[]

  funnel: Array<{ name: string; count: number; color: string }>
}

// ── Notifications ────────────────────────────────────────
export interface StaleConversation {
  id: string
  visitor_name: string | null
  minutes_waiting: number
  tunnel: 'sales' | 'support'
}

export interface AssignedTask {
  id: string
  task_number: number | null
  title: string
  priority: string
  status: string
  created_at: string
}

export interface NotificationsResponse {
  success: boolean
  stale_conversations: StaleConversation[]
  assigned_tasks: AssignedTask[]
  count: number
}

// ── Tasks ──────────────────────────────────────────────
export interface Task {
  id: string
  task_number: number | null
  title: string
  description: string | null
  status: 'todo' | 'in progress' | 'done' | 'canceled' | 'backlog'
  label: 'bug' | 'feature' | 'documentation'
  priority: 'low' | 'medium' | 'high' | 'critical'
  assignee_id: string | null
  assignee_name: string | null
  created_by: string | null
  due_date: string | null
  created_at: string
  updated_at: string
}

export interface TasksResponse {
  success: boolean
  data: Task[]
  count: number
}

export interface UserOption {
  id: string
  name: string
  email: string
  role: string
}

export interface UserAccessAuditItem {
  id: string
  target_user_id: string
  changed_by_user_id: string | null
  action: string
  old_role: string | null
  new_role: string | null
  old_tunnel_scope: string | null
  new_tunnel_scope: string | null
  old_is_active: boolean | null
  new_is_active: boolean | null
  changed_fields: string[]
  changed_by_name?: string | null
  changed_by_email?: string | null
  created_at: string
}

/** /api/admin/agents/live — who's live right now (Team live card). */
export interface LiveAgent {
  id: string
  name: string | null
  role: 'sales' | 'support' | 'supervisor' | string
  is_ready: boolean
  is_online: boolean
  last_seen: string | null
}

/** Why a panel considers itself off-screen. Any one reason is enough. */
export type PanelDormancyReason = 'tab_hidden' | 'crm_hidden' | 'not_leader'

/** Injected into installCrmBridge so the bridge stays dependency-free and
 *  node-testable: it never imports a store or a router itself. */
export interface CrmBridgeDeps {
  /** `isAllowedCrmOrigin` from crm-embed-auth — the ONE allow-list. */
  isAllowedOrigin: (origin: string) => boolean
  /** Current queue depth, read at handshake time. Injected rather than
   *  imported so this module stays dependency-free and node-testable. */
  getQueueCount?: () => number
  /** Show the queue view. The CRM sends `crm:focus-queue` right before it
   *  opens the panel for a queued conversation; without this the agent lands
   *  on whatever screen they left, on a panel that opened itself. */
  onFocusQueue?: () => void
  /** The CRM's dock was minimised or restored. An iframe hidden by CSS is not
   *  "hidden" to Page Visibility — the CRM tab is visible — so the CRM is the
   *  only one who can tell us the panel is off-screen. */
  onVisibility?: (hidden: boolean) => void
}

/** SSE message rows are not a full `Message`: the stream does not always
 *  carry conversation_id / model_used / cost. Consumers that need the rest
 *  already have the conversation in scope. */
export type SseMessageRow = Pick<Message, 'id' | 'role' | 'content' | 'created_at'> & Partial<Message>

/** Every frame the agent stream can emit. Discriminated on `event`; a message
 *  row has no `event` and is recognised by its `id`. Validated once in the
 *  reader so consumers never cast `unknown`. */
export type AgentSseEvent =
  | { event: 'typing'; is_typing: boolean; text: string }
  | { event: 'presence'; widget_open?: boolean; widget_presence?: string;
      widget_presence_effective?: string; widget_presence_age_seconds?: number;
      widget_last_close_reason?: string; widget_last_event_at?: string }
  | { event: 'stream_chunk'; delta: string }
  | ({ event: 'stream_end' } & SseMessageRow)
  | SseMessageRow   // plain message row (agent / system / ai)

export function parseAgentSseEvent(raw: unknown): AgentSseEvent | null {
  if (!raw || typeof raw !== 'object') return null
  const r = raw as Record<string, unknown>
  const ev = r.event
  if (ev === 'typing') return { event: 'typing', is_typing: Boolean(r.is_typing), text: String(r.text ?? '') }
  if (ev === 'presence') return { ...(r as object), event: 'presence' } as AgentSseEvent
  if (ev === 'stream_chunk') return { event: 'stream_chunk', delta: String(r.delta ?? '') }
  if (typeof r.id === 'string') {
    if (ev === 'stream_end') return { ...(r as object), event: 'stream_end' } as AgentSseEvent
    // A message row has NO event field. An unknown event that happens to carry
    // an id must not pass as a row — the consumer would see `event` and drop
    // the frame silently, which is worse than rejecting it here.
    if (ev === undefined) return r as unknown as SseMessageRow
  }
  return null
}
