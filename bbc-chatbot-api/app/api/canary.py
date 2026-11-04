"""Canary content routes (PR-3) — believable, entirely fake, alert on fetch.

GET /.env and GET /api/admin/export are not real endpoints; an intruder probing
for them gets plausible fake content seeded with a canarytokens.org key.
Fetching alerts us; trying the key alerts canarytokens with the attacker's IP.
Public by design (an attacker would not be logged in), and they never touch the
database. When DECOYS_ENABLED is off, the paths 404 like anything else.
"""

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from app.security import decoys

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/.env", include_in_schema=False)
async def _env_canary(request: Request):
    if not decoys.is_canary_path("/.env"):
        raise HTTPException(status_code=404, detail="Not Found")
    await decoys.alert_canary_fetch(request, "/.env")
    return PlainTextResponse(decoys.canary_body("/.env"))


@router.get("/api/admin/export", include_in_schema=False)
async def _export_canary(request: Request):
    if not decoys.is_canary_path("/api/admin/export"):
        raise HTTPException(status_code=404, detail="Not Found")
    await decoys.alert_canary_fetch(request, "/api/admin/export")
    return PlainTextResponse(
        decoys.canary_body("/api/admin/export"), media_type="application/json"
    )
