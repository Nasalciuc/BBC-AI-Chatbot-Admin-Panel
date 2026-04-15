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

export interface Conversation {
  id: string
  tunnel: 'sales' | 'support'
  mode: 'ai' | 'human' | 'waiting_for_agent'
  status: 'active' | 'pending' | 'closed' | 'needs_agent'
  visitor_name: string | null
  visitor_email: string | null
  visitor_phone: string | null
  assigned_agent_id: string | null
  message_count: number
  ai_cost_total: number
  created_at: string
  updated_at: string
  closed_at: string | null
  summary?: string | null
  messages?: Message[]
  lead?: Lead | null
  metadata?: Record<string, string>
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
  visitor_name: string | null
  visitor_email: string | null
  visitor_phone: string | null
}

export interface LeadsResponse {
  success: boolean
  data: Lead[]
  count: number
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
  cost_today: number
  cost_week:  number
  cost_month: number
  top_routes:          Array<{ route: string; count: number }>
  conversations_trend: Array<{ date: string; count: number }>
  leads_trend:         Array<{ date: string; count: number }>

  // === V2 ADDITIONS ===
  conversations_yesterday: number
  leads_uncalled:          number
  leads_sla_breach:        number
  cost_avg_30d:            number
  daily_budget:            number
  latency_median_ms:       number
  fallback_rate_percent:   number
  cost_vs_budget_percent:  number
  avg_duration_minutes:    number
  messages_total_month:    number

  conversations_trend_v2: Array<{ date: string; sales: number; support: number }>

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

export interface NotificationsResponse {
  success: boolean
  stale_conversations: StaleConversation[]
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
