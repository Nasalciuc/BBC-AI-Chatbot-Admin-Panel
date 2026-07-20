// ── BBC Admin — Type Definitions ─────────────────────────────────
// Transplanted from BBC-Admin-Frontend shared/types/index.ts
// + RBAC types added for V1 demo

export interface Lead {
  id: string
  name: string
  initials: string
  email: string
  phone: string
  route: string
  intent: 'Flight Booking' | 'Price Inquiry' | 'Route Information'
  score: number
  status: 'New' | 'Contacted' | 'Converted' | 'Lost'
  capturedAt: string
  avatarColor: string
  notes: string
  leadSource?: 'chat_sales' | 'chat_support' | 'manual' | 'import'
  tags?: string[]
}

export interface RouteCard {
  route: string
  originCode: string
  destinationCode: string
  duration: string
  airlines: string[]
  dates: string
  priceRange: string
  isNonStop: boolean
}

export interface Message {
  id: string
  role: 'visitor' | 'ai' | 'agent'
  content: string
  timestamp: string
  isLeadCapture?: boolean
  routeCard?: RouteCard
}

export interface Conversation {
  id: string
  type: 'SALES' | 'SUPPORT'
  visitorName: string
  visitorEmail: string
  messages: Message[]
  intent: string
  intentType: 'primary' | 'secondary'
  status: 'Active' | 'Pending' | 'Closed'
  duration: string
  leadCaptured: boolean
  createdAt: string
  assignedAgentId?: string
}

export interface KBEntry {
  id: string
  categoryId: string
  title: string
  content: string
  lastUpdated: string
}

export interface KBCategory {
  id: string
  name: string
  icon: string
  iconBg: string
  iconColor: string
  entries: KBEntry[]
  tunnel: 'sales' | 'support'
}

export interface Agent {
  id: string
  name: string
  initials: string
  email: string
  status: 'online' | 'offline' | 'busy'
  tunnel: 'sales' | 'support' | 'both'
  activeConversations: number
  lastActive: string
  avatarColor: string
}

export interface DashboardStats {
  totalConversations: number
  activeConversations: number
  leadsCount: number
  leadsThisWeek: number
  avgResponse: string
  kbCoverage: string
  conversionRate: string
  topRoute: string
  leadsByDay: { day: string; count: number }[]
  tunnelSplit: { sales: number; support: number }
}

export interface AppSettings {
  companyName: string
  widgetPosition: 'bottom-right' | 'bottom-left'
  salesGreeting: string
  supportGreeting: string
  businessHoursStart: string
  businessHoursEnd: string
  businessTimezone: string
  autoReplyAfterHours: boolean
  afterHoursMessage: string
  maxAIMessagesPerConv: number
  aiModel: 'haiku' | 'sonnet'
  enableLeadNotifications: boolean
  enableSlackNotifications: boolean
  slackWebhookUrl: string
}

export interface MetricEvent {
  id: string
  type: 'chat_response' | 'lead_captured' | 'handoff' | 'fallback'
  model: 'template' | 'haiku' | 'sonnet' | 'template_fallback'
  cost: number
  latencyMs: number
  intent: string
  tunnel: 'sales' | 'support'
  kbHit: boolean
  timestamp: string
}

// ── RBAC Types (NEW — for V1 demo) ──────────────────────────────

export type UserRole = 'owner' | 'admin' | 'dev' | 'qa' | 'sales' | 'support' | 'supervisor' | 'project_manager'
export type Tunnel = 'sales' | 'support' | 'all'

export interface BBCUser {
  id: string
  name: string
  email: string
  role: UserRole
  tunnel: Tunnel
  initials: string
  avatarColor: string
  team_id?: string | null
}

// Teams (Phase 1: model only — no team-based filtering yet). Mirrors the
// backend public.teams table.
export interface Team {
  id: string
  name: string
  shift_name?: string | null
  shift_start?: string | null
  shift_end?: string | null
  supervisor_id?: string | null
  pm_id?: string | null
  is_active: boolean
  created_by?: string | null
  created_at: string
  updated_at?: string | null
}

export interface Permissions {
  canViewLeads: boolean
  canEditLeads: boolean
  canViewAllConversations: boolean
  canReadMessages: boolean
  canReassignConversations: boolean
  canViewUsers: boolean
  canEditUsers: boolean
  canEditKB: boolean
  canViewKB: boolean
  canProposeKBChanges: boolean
  canViewIntegrations: boolean
  canEditSettings: boolean
  canViewAllSettings: boolean
  canViewDashboardGlobal: boolean
  canAssignTasks: boolean
  canViewTeams: boolean
  canManageTeams: boolean
  visibleTunnels: Tunnel[]
}
