"""Auth endpoints — login + invite."""

import logging
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from config.settings import settings
from app.db import supabase as db
from app.security.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    token: str
    user: dict


class InviteRequest(BaseModel):
    name: str
    email: str
    role: str = "sales"
    tunnel_scope: str = "sales"
    password: str  # Temporary password set by admin


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    """Authenticate user with email + password, return JWT."""
    user = await db.get_user_by_email(req.email.lower().strip())
    if not user:
        raise HTTPException(401, "Invalid email or password")

    # Check password
    stored_hash = user.get("password_hash")
    if not stored_hash:
        raise HTTPException(401, "Account not activated — contact admin")

    if not bcrypt.checkpw(req.password.encode("utf-8"), stored_hash.encode("utf-8")):
        raise HTTPException(401, "Invalid email or password")

    # Check active
    if not user.get("is_active", True):
        raise HTTPException(403, "Account disabled — contact admin")

    # Generate JWT
    if not settings.jwt_secret:
        raise HTTPException(500, "JWT not configured")

    payload = {
        "sub": user["id"],
        "email": user["email"],
        "name": user.get("name", ""),
        "role": user.get("role", "sales"),
        "tunnel_scope": user.get("tunnel_scope", "sales"),
        "exp": datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expiry_hours),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm="HS256")

    logger.info(f"Login OK | email={user['email']} role={user.get('role')}")

    return LoginResponse(
        token=token,
        user={
            "id": user["id"],
            "email": user["email"],
            "name": user.get("name", ""),
            "role": user.get("role", "sales"),
            "tunnel_scope": user.get("tunnel_scope", "sales"),
        },
    )


@router.post("/invite")
async def invite_user(req: InviteRequest, current_user: dict = Depends(get_current_user)):
    """Create new user with temporary password. Owner/admin only."""
    if current_user.get("role") not in ("owner", "admin", "dev"):
        raise HTTPException(403, "Only owner/admin can invite users")

    # Check email not taken
    existing = await db.get_user_by_email(req.email.lower().strip())
    if existing:
        raise HTTPException(409, "Email already exists")

    # Hash password
    password_hash = bcrypt.hashpw(
        req.password.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")

    user = await db.create_user({
        "email": req.email.lower().strip(),
        "name": req.name,
        "role": req.role,
        "tunnel_scope": req.tunnel_scope,
        "password_hash": password_hash,
        "is_active": True,
    })

    if not user:
        raise HTTPException(500, "Failed to create user")

    logger.info(f"User invited | email={req.email} role={req.role} by={current_user.get('email', 'admin')}")

    return {"success": True, "data": {"id": user["id"], "email": user["email"], "name": user["name"], "role": user["role"]}}
