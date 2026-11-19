"""Pydantic models for chat endpoint."""
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class VisitorInfo(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    country_code: Optional[str] = None  # ISO 3166-1 alpha-2 (e.g. "US", "GB")


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    conversation_id: Optional[str] = None
    visitor_id: Optional[str] = None
    tunnel: str = Field(default="sales", pattern="^(sales|support)$")
    visitor: VisitorInfo = Field(default_factory=VisitorInfo)
    metadata: Optional[dict] = None
    # A button after the card the client tapped (app.pipeline.card_buttons): "yes", "whatsapp",
    # "email" or "wait". Anything else is dropped, never a 422: the message is answered as typed.
    button: Optional[str] = None
    # The price the client saw elsewhere, typed in the optional field beside the channel (C5): kept on
    # the lead for the agent, never repeated in the chat.
    price_seen: Optional[str] = Field(default=None, max_length=40)

    @field_validator("button", mode="before")
    @classmethod
    def _known_button(cls, v):
        return v if v in ("yes", "whatsapp", "email", "wait") else None

    @field_validator("price_seen", mode="before")
    @classmethod
    def _short_price(cls, v):
        return (v.strip()[:40] or None) if isinstance(v, str) else None


class ChatResponse(BaseModel):
    model_config = {"protected_namespaces": ()}

    conversation_id: str
    message: str
    streaming: bool = False
    type: str  # "template" | "ai" | "template_fallback"
    model_used: str
    cost: float = 0.0
    route_card: Optional["RouteCard"] = None
    quick_replies: Optional[list[str]] = None
    system_messages: Optional[list[dict]] = None
    # The buttons under a template answer, with what each does ({"kind", "label"}: card_words.KINDS),
    # so the widget acts on the kind whatever the language of the label.
    buttons: Optional[list[dict]] = None
    # The stored row of a template answer: the widget swaps its own copy for it, so the poll never
    # draws it twice.
    message_id: Optional[str] = None
    created_at: Optional[str] = None


class RouteCard(BaseModel):
    origin: str
    destination: str
    airlines: str = ""
    duration: str = ""
    price_range: str = ""
