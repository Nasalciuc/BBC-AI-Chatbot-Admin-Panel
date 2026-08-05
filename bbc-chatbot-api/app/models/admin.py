"""Pydantic models for all admin endpoints."""
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field


# ── Conversations ─────────────────────────────────────────────
class ConversationListItem(BaseModel):
    id: str
    tunnel: str
    mode: str
    status: str
    visitor_name: Optional[str]
    visitor_email: Optional[str]
    visitor_phone: Optional[str]
    assigned_agent_id: Optional[str]
    message_count: int
    ai_cost_total: float
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime]


class MessageItem(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    model_used: Optional[str]
    cost: float
    created_at: datetime


class ConversationDetail(ConversationListItem):
    messages: List[MessageItem] = []
    metadata: dict = {}


class ConversationUpdate(BaseModel):
    status: Optional[str] = None
    mode: Optional[str] = None
    assigned_agent_id: Optional[str] = None
    visitor_name: Optional[str] = None
    visitor_email: Optional[str] = None
    visitor_phone: Optional[str] = None


# ── Leads ─────────────────────────────────────────────────────
class RouteSegmentItem(BaseModel):
    id: str
    lead_id: str
    segment_order: int
    origin_code: str
    origin_city: str
    destination_code: str
    destination_city: str
    departure_date: Optional[date]
    airline_code: Optional[str]
    cabin_class: str
    segment_type: str
    stay_nights: int
    is_stopover: bool
    transport_mode: str


class LeadListItem(BaseModel):
    id: str
    conversation_id: str
    trip_type: str
    cabin_class: str
    passengers: Optional[int]
    flexible_dates: bool
    origin_code: Optional[str]
    destination_code: Optional[str]
    departure_date: Optional[date]
    return_date: Optional[date]
    route_display: Optional[str]
    score: int
    tier: str
    status: str
    # Post-#161 this column holds a methodology-signals OBJECT. Legacy rows
    # still carry '[]' until migration 027 normalizes them — accept both so
    # GET /leads/{id} doesn't 500 on either shape.
    intent_signals: Union[Dict[str, Any], List[str]] = Field(default_factory=dict)
    notes: str
    created_at: datetime
    updated_at: datetime
    contacted_at: Optional[datetime]
    converted_at: Optional[datetime]
    # JOINed from conversations
    visitor_name: Optional[str] = None
    visitor_email: Optional[str] = None
    visitor_phone: Optional[str] = None
    reviewed_by_qa: bool = False
    reviewed_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None


class LeadFull(LeadListItem):
    route_segments: List[RouteSegmentItem] = []
    status_history: List[dict] = []
    qa_notes: Optional[str] = None


class LeadStatusUpdate(BaseModel):
    status: str
    notes: Optional[str] = None


class LeadReviewUpdate(BaseModel):
    reviewed: bool
    qa_notes: Optional[str] = None


# ── Users ────────────────────────────────────────────────────
class UserUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    tunnel_scope: Optional[str] = None
    is_active: Optional[bool] = None
    phone: Optional[str] = None
    avatar_url: Optional[str] = None
    # Nullable: null moves the user to the unassigned pool. The endpoint uses
    # model_fields_set to distinguish "not sent" from an explicit null.
    team_id: Optional[str] = None


# ── KB ────────────────────────────────────────────────────────
class KBCategoryItem(BaseModel):
    id: str
    name: str
    tunnel: str
    icon: str
    sort_order: int
    entry_count: int = 0


class KBEntryItem(BaseModel):
    id: str
    category_id: str
    title: str
    content: str
    tunnel: str
    is_active: bool
    view_count: int
    created_at: datetime
    updated_at: datetime


class KBEntryCreate(BaseModel):
    category_id: str
    title: str
    content: str
    tunnel: str
    is_active: bool = True


class KBEntryUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    is_active: Optional[bool] = None
    category_id: Optional[str] = None


# ── Dashboard ─────────────────────────────────────────────────
class TopRoute(BaseModel):
    route: str
    count: int

class TrendItem(BaseModel):
    date: str
    count: int

class TrendItemV2(BaseModel):
    date: str
    sales: int
    support: int

class HotLead(BaseModel):
    id: str
    visitor_name: Optional[str] = None
    route: str
    score: int
    tier: str
    minutes_since_created: int

class FunnelItem(BaseModel):
    name: str
    count: int
    color: str

class DashboardStats(BaseModel):
    # V1 fields
    conversations_today: int = 0
    conversations_week: int = 0
    conversations_month: int = 0
    conversations_active: int = 0
    leads_total: int = 0
    leads_new: int = 0
    leads_contacted: int = 0
    leads_qualified: int = 0
    leads_converted: int = 0
    leads_lost: int = 0
    leads_gold: int = 0
    leads_silver: int = 0
    leads_bronze: int = 0
    cost_today: Optional[float] = None
    cost_week: Optional[float] = None
    cost_month: Optional[float] = None
    top_routes: List[TopRoute] = []
    conversations_trend: List[TrendItem] = []
    leads_trend: List[TrendItem] = []

    # V2 fields
    conversations_yesterday: int = 0
    leads_uncalled: int = 0
    leads_sla_breach: int = 0
    cost_avg_30d: Optional[float] = None
    daily_budget: Optional[float] = None
    latency_median_ms: Optional[int] = None
    fallback_rate_percent: Optional[float] = None
    cost_vs_budget_percent: Optional[float] = None
    avg_duration_minutes: float = 0.0
    messages_total_month: Optional[int] = None
    conversations_trend_v2: Optional[List[TrendItemV2]] = None
    hot_leads: List[HotLead] = []
    leads_sparkline_7d: List[int] = []
    funnel: List[FunnelItem] = []
