"""Regression: GET /leads/{id} must accept dict-shaped intent_signals (#161).

The drawer sat on skeletons forever because LeadListItem declared List[str]
while the pipeline now writes {"occasion":…,"persona":…}. FastAPI response
validation 500'd → React Query retried → blank white panel. QA hits it first
because they open every NEW lead.
"""

import sys
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.admin import LeadFull
from app.security.auth import get_current_user

MASON_SIGNALS = {
    "occasion": "anniversary",
    "persona": "experience_seeker",
    "persona_source": "occasion",
}


def _lead_payload(signals) -> dict:
    now = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
    return {
        "id": "lead-mason-1",
        "conversation_id": "conv-1",
        "trip_type": "round_trip",
        "cabin_class": "business",
        "passengers": 2,
        "flexible_dates": False,
        "origin_code": "JFK",
        "destination_code": "LHR",
        "departure_date": "2026-09-01",
        "return_date": "2026-09-10",
        "route_display": "JFK → LHR",
        "score": 85,
        "tier": "gold",
        "status": "new",
        "intent_signals": signals,
        "notes": "Chat: Occasion=anniversary | Type=experience_seeker",
        "created_at": now,
        "updated_at": now,
        "contacted_at": None,
        "converted_at": None,
        "visitor_name": "Ada",
        "visitor_email": "ada@example.com",
        "visitor_phone": "+12025550100",
        "route_segments": [],
        "status_history": [],
        "qa_notes": None,
    }


class TestLeadFullValidation:
    def test_accepts_mason_dna_dict_shape(self):
        lead = LeadFull.model_validate(_lead_payload(MASON_SIGNALS))
        assert lead.intent_signals == MASON_SIGNALS
        assert isinstance(lead.intent_signals, dict)

    def test_accepts_legacy_empty_array(self):
        lead = LeadFull.model_validate(_lead_payload([]))
        assert lead.intent_signals == []

    def test_accepts_post_migration_empty_object(self):
        lead = LeadFull.model_validate(_lead_payload({}))
        assert lead.intent_signals == {}

    def test_default_is_empty_dict(self):
        payload = _lead_payload(MASON_SIGNALS)
        del payload["intent_signals"]
        lead = LeadFull.model_validate(payload)
        assert lead.intent_signals == {}


def _client(role: str = "qa"):
    from app.api.leads import router

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "u-qa",
        "email": f"{role}@bbc.com",
        "role": role,
        "tunnel_scope": "all",
    }
    return TestClient(app)


class TestGetLeadEndpoint:
    def test_qa_gets_200_with_dict_signals(self):
        """The exact failure mode: response_model=LeadFull used to 500 here."""
        with patch(
            "app.api.leads.db.get_lead_full",
            AsyncMock(return_value=_lead_payload(MASON_SIGNALS)),
        ):
            resp = _client("qa").get("/api/leads/lead-mason-1")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["intent_signals"] == MASON_SIGNALS
        assert body["id"] == "lead-mason-1"

    def test_legacy_array_still_returns_200(self):
        with patch(
            "app.api.leads.db.get_lead_full",
            AsyncMock(return_value=_lead_payload([])),
        ):
            resp = _client("qa").get("/api/leads/lead-mason-1")
        assert resp.status_code == 200, resp.text
        assert resp.json()["intent_signals"] == []

    def test_owner_also_unblocked(self):
        with patch(
            "app.api.leads.db.get_lead_full",
            AsyncMock(return_value=_lead_payload(MASON_SIGNALS)),
        ):
            resp = _client("owner").get("/api/leads/lead-mason-1")
        assert resp.status_code == 200
        assert resp.json()["intent_signals"]["persona"] == "experience_seeker"
