"""TypeAdapter contracts at the Supabase read boundary — observe-first.

A valid row passes silently; a mutant row logs at WARNING with its id and
STILL returns raw — production never breaks on a surprise shape. Tightening
to raise comes later, once the logs run quiet.
"""

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.models.rows import (
    observe_conversation_row,
    observe_lead_row,
)

_CONV = {
    "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    "tunnel": "sales",
    "status": "active",
    "mode": "ai",
    "visitor_name": "Sheniquia",
    "visitor_email": "s@example.com",   # TOP-LEVEL column, not metadata
    "visitor_phone": "+15551234567",    # TOP-LEVEL column, not metadata
    "chat_number": 1042,                # TOP-LEVEL column, not metadata
    "metadata": {"widget_presence": "online"},
    "last_user_message_at": None,
}

_LEAD_NEW = {
    "id": "L1",
    "conversation_id": _CONV["id"],
    "trip_type": "round_trip",
    "cabin_class": "business",
    "passengers": 2,
    "origin_code": "JFK",
    "destination_code": "LHR",
    "score": 80,
    "tier": "gold",
    "intent_signals": {"persona": "experience_seeker"},
    "notes": "",
}

_LEAD_LEGACY = {**_LEAD_NEW, "intent_signals": ["legacy", "array", "shape"]}


class TestValidRowsPassSilently:
    def test_conversation_row(self, caplog):
        with caplog.at_level(logging.WARNING):
            out = observe_conversation_row(_CONV)
        assert out is _CONV
        assert not [r for r in caplog.records if "[rows]" in r.message]

    def test_new_lead_row(self, caplog):
        with caplog.at_level(logging.WARNING):
            out = observe_lead_row(_LEAD_NEW)
        assert out is _LEAD_NEW
        assert not [r for r in caplog.records if "[rows]" in r.message]

    def test_legacy_array_signals_are_legal_until_migrated(self, caplog):
        # 93% of lead rows — the contract must accept them without noise.
        with caplog.at_level(logging.WARNING):
            out = observe_lead_row(_LEAD_LEGACY)
        assert out is _LEAD_LEGACY
        assert not [r for r in caplog.records if "[rows]" in r.message]


class TestMutantRowsLogAndStillReturn:
    def test_mutant_conversation_logs_with_id_and_returns_raw(self, caplog):
        mutant = {**_CONV, "chat_number": "not-a-number", "metadata": "not-a-dict"}
        with caplog.at_level(logging.WARNING):
            out = observe_conversation_row(mutant)
        assert out is mutant  # raw dict returned — observe-first
        warnings = [r.message for r in caplog.records if "[rows]" in r.message]
        assert warnings and _CONV["id"] in warnings[0]

    def test_mutant_lead_logs_and_returns_raw(self, caplog):
        mutant = {**_LEAD_NEW, "passengers": "two", "score": "high"}
        with caplog.at_level(logging.WARNING):
            out = observe_lead_row(mutant)
        assert out is mutant
        assert [r for r in caplog.records if "[rows]" in r.message]

    def test_non_dict_inputs_pass_through_untouched(self):
        assert observe_conversation_row(None) is None
        assert observe_lead_row("weird") == "weird"


class TestObservationIsInfallible:
    """The detail-load-500 hotfix: no input may ever raise out of observe."""

    def test_observe_row_with_none_never_raises(self):
        from app.models.rows import LEAD_ROW, observe_row

        assert observe_row(LEAD_ROW, None, "lead") is None
        assert observe_row(LEAD_ROW, 42, "lead") == 42

    def test_adapter_misbehavior_never_breaks_the_read(self, caplog):
        import logging
        from unittest.mock import MagicMock
        from app.models.rows import observe_row

        broken_adapter = MagicMock()
        broken_adapter.validate_python.side_effect = TypeError("adapter exploded")
        row = {"id": "R1"}
        with caplog.at_level(logging.WARNING):
            out = observe_row(broken_adapter, row, "lead")
        assert out is row
        assert [r for r in caplog.records if "observation failed" in r.message]

    def test_typed_dict_comes_from_typing_extensions(self):
        # pydantic's TypeAdapter over typing.TypedDict RAISES at import on
        # Python < 3.12 — Railway runs 3.11 while local dev runs 3.13, so
        # only this source pin keeps the prod bomb from coming back.
        import inspect
        from app import models

        src = inspect.getsource(__import__("app.models.rows", fromlist=["rows"]))
        assert "from typing_extensions import TypedDict" in src
        assert "from typing import Any, Optional\n" in src  # no typing.TypedDict


class TestWiredIntoReadHelpers:
    def test_read_helpers_observe(self):
        import inspect
        from app.db import supabase as db

        simple_src = inspect.getsource(db.get_conversation_simple)
        assert "observe_conversation_row" in simple_src
        conv_src = inspect.getsource(db.get_conversation)
        assert "observe_conversation_row" in conv_src
        assert "observe_lead_row" in conv_src
        lead_src = inspect.getsource(db.get_lead_full)
        assert "observe_lead_row" in lead_src
