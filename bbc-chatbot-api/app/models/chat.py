"""Pydantic models for chat endpoint."""
from typing import Optional
from pydantic import BaseModel, Field


class VisitorInfo(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    conversation_id: Optional[str] = None
    tunnel: str = Field(default="sales", pattern="^(sales|support)$")
    visitor: VisitorInfo = Field(default_factory=VisitorInfo)
    metadata: Optional[dict] = None


class ChatResponse(BaseModel):
    conversation_id: str
    message: str
    type: str  # "template" | "ai" | "template_fallback"
    model_used: str
    cost: float = 0.0
    route_card: Optional["RouteCard"] = None


class RouteCard(BaseModel):
    origin: str
    destination: str
    airlines: str = ""
    duration: str = ""
    price_range: str = ""
