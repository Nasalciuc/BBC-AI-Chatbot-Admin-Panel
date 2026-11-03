#!/usr/bin/env python3
"""Seed the decoy account and lead (PR-3). Idempotent. Run with `railway run`.

Creates one users row (is_decoy, is_active=false, a long random password) and
one leads+conversations pair (is_decoy) whose email/phone the owner monitors
(DECOY_LEAD_EMAIL / DECOY_LEAD_PHONE, step 0.6). Both are filtered out of every
server-side list, so the only thing that ever touches them is an intruder.

Running it twice changes nothing: it matches on the decoy email.
"""
import os
import secrets
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import bcrypt

from app.db.supabase import get_client


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def main() -> int:
    db = get_client()
    acct_email = os.environ.get("DECOY_ACCOUNT_EMAIL", "ops-backup@buybusinessclass.com")
    lead_email = os.environ.get("DECOY_LEAD_EMAIL", "").strip()
    lead_phone = os.environ.get("DECOY_LEAD_PHONE", "").strip()

    existing = db.table("users").select("id").eq("email", acct_email).execute()
    if existing.data:
        print(f"decoy account already present: {acct_email}")
    else:
        db.table("users").insert({
            "email": acct_email,
            "name": "Ops Backup",
            "role": "owner",
            "tunnel_scope": "all",
            "is_active": False,
            "is_decoy": True,
            "password_hash": _hash(secrets.token_urlsafe(32)),
        }).execute()
        print(f"created decoy account: {acct_email}")

    if lead_email or lead_phone:
        conv = db.table("conversations").select("id").eq("visitor_email", lead_email).eq("is_decoy", True).execute()
        if conv.data:
            print("decoy lead already present")
        else:
            c = db.table("conversations").insert({
                "visitor_name": "Jonathan Reeve",
                "visitor_email": lead_email or None,
                "visitor_phone": lead_phone or None,
                "tunnel": "sales",
                "status": "closed",
                "mode": "ai",
                "is_decoy": True,
            }).execute()
            cid = c.data[0]["id"]
            db.table("leads").insert({
                "conversation_id": cid,
                "origin_code": "JFK", "destination_code": "DXB",
                "trip_type": "round_trip", "passengers": 2,
                "score": 88, "tier": "gold", "status": "new",
                "is_decoy": True,
            }).execute()
            print("created decoy lead")
    else:
        print("DECOY_LEAD_EMAIL/PHONE not set — skipped the decoy lead (step 0.6)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
