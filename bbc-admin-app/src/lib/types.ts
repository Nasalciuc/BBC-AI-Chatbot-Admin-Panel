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
  /** List rows only: the newest non-system message, trimmed to 140 chars.
   *  Absent for supervisors and PMs, and when the preview read failed.
   *  An agent gets null on a chat that isn't theirs (blind take). */
  last_message?: { role: Message['role']; content: string; created_at: string } | null
  /** List rows only: the route the pipeline extracted (lead origin → destination).
   *  Never sent to an agent for a chat that isn't theirs (blind take). */
  route?: { from: string; to: string } | null
  /** When the chat entered the shared queue (waiting for a person). */
  queued_at?: string | null
  /** Presence derived on read (online / minimized / left / stale), list rows. */
  client_presence?: string | null
  client_presence_age_seconds?: number | null
  /** Country of the client's phone number (detail payload). */
  visitor_phone_country?: string | null
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
  /** Everyone flying: adults + children + infants (the CRM takes adults as the rest). */
  passengers: number | null
  children_count?: number | null
  infant_count?: number | null
  /** NOT NULL DEFAULT true and never written by the pipeline: says nothing.
   *  The client's own answer is intent_signals.date_flexible. */
  flexible_dates: boolean
  origin_code: string | null
  destination_code: string | null
  departure_date: string | null
  return_date: string | null
  route_display: string | null
  score: number
  tier: 'gold' | 'silver' | 'bronze'
  status: 'new' | 'contacted' | 'qualified' | 'converted' | 'lost'
  /** jsonb object from the pipeline: persona, occasion, nonstop_only,
   *  callback_requested, airline_preference, date_flexible, … */
  intent_signals: Record<string, unknown> | null
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

// ── Your day (GET /api/agent/day) ─────────────────────────────
export interface AgentDayBucket {
  took: number
  wait_median_s: number | null
  first_reply_median_s: number | null
  went_back: number
  closed: number
  with_lead: number
  in_crm: number
}

export interface AgentDay {
  days: 1 | 7 | 30
  tz_offset_minutes: number
  now: string
  range_start: string
  totals: AgentDayBucket & { still_open: number }
  /** Newest first; at least 7 rows. */
  by_day: Array<AgentDayBucket & { date: string }>
  went_back: Array<{ conversation_id: string; first_name: string; at: string }>
  not_in_crm: Array<{ conversation_id: string; first_name: string; has_phone: boolean; has_email: boolean; status: string | null }>
  waiting_on_you: Array<{ conversation_id: string; first_name: string; minutes: number; left_minutes_ago: number | null }>
  first_reply_week: { this: number | null; previous: number | null }
}

// ── The owner's Dashboard (GET /api/dashboard/owner) ──────────
export interface OwnerCohort {
  started: number
  wrote: number
  contact_only: number
  ready: number
  ready_ai_only: number
  ready_with_agent: number
  ai_only: number
  ai_only_ready: number
  with_agent: number
}

/** A chat a number points at, for "Open". */
export interface ChatRef {
  id: string
  first_name: string
  chat_number: number | null
}

export type LeaveKey =
  | 'from' | 'to' | 'route' | 'dates' | 'flexible' | 'travelers' | 'occasion' | 'contact'
  | 'summary' | 'specialist' | 'callback' | 'goodbye' | 'phone' | 'other'

export interface LeaveRow {
  key: LeaveKey
  label: string
  chats: number
  before: number
  conversations: ChatRef[]
}

export interface SourceRow {
  name: string
  chats: number
  wrote: number
  ready: number
  /** On the "Other" row: how many names it groups. */
  names?: number
}

export interface AgentBlock {
  given: number
  first_reply_median_s: number | null
  replies_measured: number
  replies_over_deadline: number
  went_back: number
  after_yes: number
  never_wrote: number
}

export interface AgentRow {
  user_id: string
  name: string
  role: string | null
  ready_hours: number
  chats: number
  first_reply_median_s: number | null
  first_reply_before_s: number | null
  went_back: number
  after_yes: number
  never_wrote: number
  not_in_crm: number
  not_in_crm_conversations: ChatRef[]
}

export interface HourRow {
  hour: number
  given: number
  went_back: number
  ready_people: number
}

export interface AiReviewRow {
  key: 'phone' | 'price' | 'repeat'
  chats: number
  left_after: number
  conversations: ChatRef[]
}

export interface CrmBreakdown {
  reached: number
  confirmed: number
  from_agent_chats: number
  defaults: number
  not_in_crm: number
  closed_by_agent: number
  refused: number
  refused_reasons: Record<string, number>
  not_sent_yet: number
}

export type AttentionItem =
  | { kind: 'agent'; level: 'urgent'; user_id: string; name: string; went_back: number; chats: number; first_reply_s: number; ready_hours: number }
  | { kind: 'hour'; level: 'check'; hour: number; went_back: number; given: number; ready_people: number }
  | { kind: 'review'; level: 'check'; waiting: number; waiting_long: number; last_review_at: string | null }
  | { kind: 'ai_latency'; level: 'check'; latency_ms: number; line_ms: number }

export type SourceTab = 'source' | 'site' | 'device' | 'page' | 'route'

export interface OwnerDashboard {
  days: 1 | 7 | 30
  tz_offset_minutes: number
  now: string
  range_start: string
  previous: { start: string; end: string }
  sites: string[]
  overview: {
    now: OwnerCohort
    before: OwnerCohort
    /** Oldest first; "Today" still has 7 bars. */
    by_day: Array<{ date: string; ready: number; other: number; contact_only: number }>
  }
  funnel: {
    /** Route, + a date in the future, + travelers and the return, confirmed, ready. */
    ai_only: number[]
    with_agent: number[]
    time_to_ready_median_s: number | null
  }
  leave: { total: number; total_before: number; rows: LeaveRow[] }
  /** null for supervisor, PM and QA: marketing sources are the owner's. */
  sources: Record<SourceTab, SourceRow[]> | null
  source_spellings: string[]
  agents: {
    now: AgentBlock
    before: AgentBlock
    by_agent: AgentRow[]
    by_hour: HourRow[]
    ready_hours_before: Record<string, number>
  }
  quality: {
    ready: number
    reviewed: number
    to_review: number
    waiting_long: number
    oldest_waiting_since: string | null
    last_review_at: string | null
    qa_accounts: number
    sent_to_phone: number
    sent_to_phone_conversations: ChatRef[]
    ai_review: AiReviewRow[]
    /** null for supervisor, PM and QA: the AI's cost and answers are the owner's. */
    ai: {
      cost: number
      cost_per_ready: number | null
      answers: number
      template_answers: number
      latency_median_ms: number | null
      latency_line_ms: number
    } | null
    crm: CrmBreakdown
  }
  attention: AttentionItem[]
  /** Whose chats these are. team_scoped_links: the chat links keep only the
   *  viewer's own teams' chats (the ones Conversations opens for them). */
  scope: { tunnel: 'all' | 'sales' | 'support'; team_scoped_links: boolean }
}

// ── Leads page (GET /api/leads?view=…) ────────────────────────
export type LeadLevel = 'ready' | 'almost' | 'early' | 'contact'
export type LeadsView = 'not_in_crm' | 'to_review' | 'reviewed' | 'all'
export type LeadsOrder = 'newest' | 'oldest' | 'closest'
export type LeadCrmState =
  | 'in_crm' | 'no_phone' | 'refused' | 'open' | 'closed_by_agent' | 'ready_never_sent' | 'never_sent'

/** A lead as the Leads page lists it: the lead, its chat's fields, and the
 *  level / chat-with / CRM state the server derived (one definition for the
 *  rows, the tabs and the counts). */
export interface LeadRow {
  id: string
  conversation_id: string
  chat_number: number | null
  created_at: string
  updated_at?: string
  origin_code: string | null
  destination_code: string | null
  route_display: string | null
  departure_date: string | null
  return_date: string | null
  passengers: number | null
  children_count?: number | null
  infant_count?: number | null
  trip_type: Lead['trip_type'] | null
  cabin_class: Lead['cabin_class'] | null
  flexible_dates: boolean | null
  score: number
  tier: Lead['tier']
  status: Lead['status']
  team_id?: string | null
  created_in_crm: boolean | null
  created_in_crm_at: string | null
  crm_lead_id: string | null
  crm_push_gate_reason: string | null
  crm_push_attempts?: number | null
  reviewed_by_qa: boolean | null
  reviewed_at: string | null
  reviewed_by: string | null
  /** QA's notes, saved with the review. */
  qa_notes?: string | null
  visitor_name: string | null
  visitor_email: string | null
  visitor_phone: string | null
  conversation_status: string | null
  conversation_mode: string | null
  tunnel: string | null
  assigned_agent_id: string | null
  last_agent_message_at: string | null
  conversation_closed_at: string | null
  confirmed_at: string | null
  closed_by_agent_id: string | null
  level: LeadLevel
  chat_with: 'person' | 'ai'
  crm_state: LeadCrmState
  agent_name: string | null
  closed_by_name: string | null
}

export interface LeadsViewResponse {
  success: boolean
  data: LeadRow[]
  count: number
  counts: Record<LeadsView, number>
  order: LeadsOrder
  summary?: {
    open: number
    closed_by_agent: number
    ready_never_sent: number
    never_sent: number
    refused: Record<string, number>
  }
  /** QA's cards, on To review and Reviewed. */
  review?: { to_review: number; reviewed_today_by_me: number; ai_only_to_review: number }
}

export interface EarlierLead {
  conversation_id: string
  chat_number: number | null
  created_at: string | null
  status: string | null
  lead_id: string | null
  route: string | null
  in_crm: boolean
  crm_lead_id: string | null
}

// ── Users & access ────────────────────────────────────────────
export interface PanelUser {
  id: string
  email: string
  name: string | null
  role: string
  tunnel_scope: string | null
  avatar_url: string | null
  is_active: boolean
  last_seen_at: string | null
  phone: string | null
  team_id: string | null
  created_at: string
  updated_at?: string
  chat_enabled?: boolean
  /** Owner/admin/dev only. Never the hash. */
  has_password?: boolean
  /** The newest set-password invite (owner/admin/dev only). */
  invite?: { expires_at: string | null; used_at: string | null; sent_at: string | null; sent_by: string | null } | null
}

export type AccessEventKind =
  | 'invited' | 'invite_again' | 'role' | 'tunnel' | 'deactivated' | 'reactivated'
  | 'chats_on' | 'chats_off' | 'password_reset'

export interface AccessEvent {
  at: string
  kind: AccessEventKind
  actor_id: string | null
  actor_name: string | null
  target_id: string | null
  target_name: string | null
  old?: string | null
  new?: string | null
  /** For invites: the role it was sent for. */
  role?: string | null
}

export interface Handover {
  open_chats: Array<{ id: string; chat_number: number | null; tunnel: string | null; first_name: string }>
  clients_90_days: number
  leads: number
  window_days: number
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

// ── To read (GET /api/conversations/to-read, /conversations/{id}/reading) ──
export type LostReason = 'back' | 'anyone' | 'never' | 'yes'

export interface ToReadRow {
  id: string
  chat_number: number | null
  kind: 'lost' | 'sample' | 'help'
  at: string
  /** "Olga V.", or the masked name for a supervisor. */
  client: string
  agent_id: string | null
  agent_name: string | null
  route: string | null
  status: string | null
  reasons: { key: LostReason; at: string; user_id: string | null; name: string | null }[]
}

export interface ToReadList {
  week_start: string
  sample_week: { start: string; end: string }
  lost: ToReadRow[]
  sample: ToReadRow[]
  people: { id: string; name: string | null }[]
  no_team: boolean
}

export interface ReadingEvent {
  at: string
  kind: 'started' | 'taken' | 'first_reply' | 'went_back' | 'closed' | 'not_ready' | 'ended'
  user_id?: string | null
  name?: string | null
  seconds?: number
}

export interface Reading {
  id: string
  chat_number: number | null
  client: string
  status: string | null
  tunnel: string | null
  created_at: string | null
  closed_at: string | null
  person: { user_id: string; name: string | null } | null
  messages: { id: string | null; role: 'user' | 'ai' | 'agent' | 'system'; at: string; text: string | null; user_id: string | null; name: string | null }[]
  events: ReadingEvent[]
  reasons: ToReadRow['reasons']
  lead: {
    id: string | null
    route: string | null
    origin_code: string | null
    destination_code: string | null
    departure_date: string | null
    return_date: string | null
    passengers: number | null
    trip_type: string | null
    cabin_class: string | null
    level: LeadLevel
    crm_state: LeadCrmState
    crm_lead_id: string | null
    crm_push_gate_reason: string | null
    phone: string | null
    email: string | null
  } | null
  week: { user_id: string; name: string | null; chats: number; went_back: number; since: string } | null
}

// ── Team · Now (GET /api/admin/team/now, /admin/team/people/{id}/today) ──
export type FloorPresence = 'ready' | 'no_signal' | 'online' | 'offline'

export interface FloorChat {
  id: string
  chat_number: number | null
  tunnel: 'sales' | 'support' | string | null
  client: string
  route: string | null
  messages: number | null
  preview: string | null
  since: string | null
}

export interface FloorPerson {
  id: string
  name: string | null
  role: string
  tunnel: 'sales' | 'support' | null
  team_id: string | null
  team_name: string | null
  presence: FloorPresence
  ready_since: string | null
  last_seen_at: string | null
  chats_now: number
  took_today: number
  first_reply_today_s: number | null
  went_back_today: number
}

export type FloorAttention =
  | { kind: 'client_waiting'; level: 'urgent'; user_id: string; name: string | null; conversation_id: string; client: string; chat_number: number | null; minutes: number }
  | { kind: 'no_signal'; level: 'check'; user_id: string; name: string | null; minutes: number | null; off_after_minutes: number }
  | { kind: 'queue_wait'; level: 'check'; tunnel: string; conversation_id: string; client: string; chat_number: number | null; minutes: number; ready: number; ready_chats: number[] }
  | { kind: 'coverage'; level: 'check'; tunnel: string; user_id: string; name: string | null; since: string | null }
  | { kind: 'went_back'; level: 'info'; user_id: string; name: string | null; at: string; conversation_id: string; presence: FloorPresence | null; last_seen_at: string | null }

/** A team on Teams & shifts, with today's numbers and Ready per hour. */
export interface TeamCard {
  id: string
  name: string | null
  shift_start: string | null
  shift_end: string | null
  supervisor_name: string | null
  pm_name: string | null
  members: { id: string; name: string | null }[]
  ready_now: number
  won_today: number
  back_today: number
  /** 24 hours of the viewer's day: how many were Ready; null = not yet. */
  coverage: (number | null)[]
}

export interface TeamNow {
  no_team: boolean
  now: string
  day_start?: string
  stats?: {
    ready: number
    no_signal: number
    online: number
    waiting: number
    oldest_waiting: { client: string; since: string } | null
    first_reply_today_s: number | null
    replies_today: number
    back_today: number
  }
  attention?: FloorAttention[]
  queue?: (FloorChat & { asked_for_person: boolean })[]
  with_person?: (FloorChat & { agent_id: string; agent_name: string | null; client_waiting: boolean })[]
  ai_only?: { count: number; chats: FloorChat[] }
  people?: FloorPerson[]
  teams?: TeamCard[]
  windows?: { queue_s: number; ready_off_s: number }
  can_give?: boolean
}

export interface PersonDay {
  user: {
    id: string
    name: string | null
    email: string | null
    role: string
    tunnel: 'sales' | 'support' | null
    is_active: boolean | null
    team: { id: string; name: string | null; shift_start: string | null; shift_end: string | null } | null
    presence: FloorPresence
    ready_since: string | null
    last_seen_at: string | null
  }
  today: {
    chats_now: number
    took: number
    first_reply_median_s: number | null
    replied_to: number
    went_back: number
    takes_lost: number
    minutes_online: number
    first_seen_at: string | null
  }
  ready_today: [string, string | null][]
  week_online: { date: string; minutes_online: number }[]
  activity: {
    at: string
    kind: 'ready' | 'not_ready' | 'took' | 'replied' | 'went_back' | 'closed' | 'take_lost' | 'client_waiting'
    conversation_id?: string
    client?: string
    seconds?: number
    live?: boolean
  }[]
  day_start: string
}
