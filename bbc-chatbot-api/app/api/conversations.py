"""Admin API — conversations CRUD."""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from app.db import supabase as db
from app.models.admin import ConversationUpdate
from app.security.auth import get_current_user
from app.security.input_sanitizer import sanitize_message
from app.services.conversation_service import add_message

router = APIRouter()


def _enforce_tunnel(user: dict, tunnel: Optional[str]) -> Optional[str]:
    """Force tunnel filter for sales/support roles."""
    role = user.get("role", "sales")
    if role in ("owner", "admin", "dev"):
        return tunnel  # privileged users can filter freely
    scope = user.get("tunnel_scope", role)
    if tunnel and tunnel != scope:
        raise HTTPException(status_code=403, detail="Access denied to this tunnel")
    return scope


@router.get("/conversations")
async def list_conversations(
    tunnel: Optional[str] = Query(None, pattern="^(sales|support)$"),
    status: Optional[str] = Query(None, pattern="^(active|pending|closed)$"),
    assigned_to: Optional[str] = Query(None, pattern="^(me|none|all)$"),
    search: Optional[str] = Query(None, max_length=100),
    limit:  int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(get_current_user),
):
    try:
        tunnel = _enforce_tunnel(user, tunnel)

        # Resolve assigned_to into an agent_id filter
        agent_id_filter: Optional[str] = None
        agent_id_is_null: bool = False
        if assigned_to == "me":
            agent_id_filter = user.get("id")
        elif assigned_to == "none":
            agent_id_is_null = True
        # "all" or None → no agent filter (owner/admin sees everything)

        rows, total = await db.get_conversations(
            tunnel=tunnel, status=status, search=search,
            agent_id=agent_id_filter, agent_id_is_null=agent_id_is_null,
            limit=limit, offset=offset,
        )
        return {"success": True, "data": rows, "count": total}
    except Exception as e:
        return {"success": False, "data": [], "count": 0, "error": str(e)}


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    try:
        conv = await db.get_conversation(conversation_id)
        if not conv:
            return {"success": False, "data": None, "count": 0, "error": "Not found"}
        return {"success": True, "data": conv, "count": 1}
    except Exception as e:
        return {"success": False, "data": None, "count": 0, "error": str(e)}


@router.patch("/conversations/{conversation_id}")
async def update_conversation(conversation_id: str, body: ConversationUpdate):
    payload = body.model_dump(exclude_none=True)
    if not payload:
        return {"success": False, "data": None, "count": 0, "error": "No fields to update"}
    try:
        result = await db.update_conversation(conversation_id, payload)
        if not result:
            return {"success": False, "data": None, "count": 0, "error": "Conversation not found"}
        return {"success": True, "data": result, "count": 1}
    except Exception as e:
        return {"success": False, "data": None, "count": 0, "error": str(e)}


class AgentMessageBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)


@router.post("/conversations/{conversation_id}/messages")
async def send_agent_message(
    conversation_id: str,
    body: AgentMessageBody,
    user: dict = Depends(get_current_user),
):
    """Agent sends a message in a conversation. Auto-sets mode to 'human'."""
    # 1. Verify conversation exists and agent has tunnel access
    conv = await db.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _enforce_tunnel(user, conv.get("tunnel"))

    # 2. Sanitize agent message (same rules as visitor messages)
    clean_content = sanitize_message(body.content)

    # 3. Save message with role='agent'
    msg = await add_message(
        conversation_id=conversation_id,
        role="agent",
        content=clean_content,
    )

    # 4. Auto-set mode to 'human' and assign this agent
    await db.update_conversation(conversation_id, {
        "mode": "human",
        "assigned_agent_id": user.get("id"),
    })

    return {"success": True, "data": msg}


@router.post("/conversations/{conversation_id}/claim")
async def claim_conversation(
    conversation_id: str,
    user: dict = Depends(get_current_user),
):
    """Operator claims an unassigned conversation from the queue."""
    conv = await db.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _enforce_tunnel(user, conv.get("tunnel"))

    # Check if already assigned to someone else
    current_agent = conv.get("assigned_agent_id")
    if current_agent and current_agent != user.get("id"):
        raise HTTPException(status_code=409, detail="Conversation already taken by another agent")

    await db.update_conversation(conversation_id, {
        "mode": "human",
        "assigned_agent_id": user.get("id"),
    })
    return {"success": True, "data": {"conversation_id": conversation_id, "assigned_to": user.get("id")}}


@router.post("/conversations/{conversation_id}/close")
async def close_conversation(
    conversation_id: str,
    user: dict = Depends(get_current_user),
):
    """Close a conversation. Sets status=closed and closed_at."""
    conv = await db.get_conversation(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    _enforce_tunnel(user, conv.get("tunnel"))

    from datetime import datetime, timezone
    await db.update_conversation(conversation_id, {
        "status": "closed",
        "closed_at": datetime.now(timezone.utc).isoformat(),
    })
    return {"success": True, "data": {"conversation_id": conversation_id, "status": "closed"}}
