"""Wave 6 hardening pack — four independent fixes, one branch.

031  the advisor findings: blocklist without RLS, role helpers callable by
     anon, functions with no pinned search_path.
ROUTE/FLOW  the model diminished routes ("that's a short hop" on a trip
     someone is buying business class for), invented operations it cannot
     see ("connecting through Chicago or Atlanta"), and ended replies
     without a question — DIANA answered "Maybe" and the conversation
     simply stopped. JOSEF typed his number and asked to be rung; the
     pipeline asked what he meant.
INVITE  a double-clicked button minted twin tokens 240ms apart and the
     second invalidated the one already in Kate's inbox; a 24h link that
     landed on Friday was dead by Monday (Mitch never used his); every
     failure said the same thing.
PRESENCE  9 of the 34 vanish-census conversations still read `online`
     with no close event ever recorded — the beacon never landed and
     nothing contradicted it, so operators talked to ghosts.
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

ROOT = os.path.join(os.path.dirname(__file__), "..")


def _read(*parts: str) -> str:
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


# ════════════════════════════════════════════════════════════
# 031 — security hardening (migration contract)
# ════════════════════════════════════════════════════════════

class TestMigration031:
    def setup_method(self):
        self.sql = _read("migrations", "031_security_hardening.sql")

    def test_blocklist_gets_rls(self):
        assert "ALTER TABLE public.blocklist ENABLE ROW LEVEL SECURITY" in self.sql

    def test_role_helpers_are_revoked_from_public_not_just_anon(self):
        """Verified live: both carry `=X/postgres`, the implicit grant to
        PUBLIC. Revoking from `anon` alone is a NO-OP — anon keeps EXECUTE
        through PUBLIC membership."""
        assert "FROM PUBLIC, anon;" in self.sql
        assert self.sql.count("REVOKE EXECUTE ON FUNCTION") == 2
        # …and the roles that legitimately call them keep the grant.
        assert "GRANT  EXECUTE ON FUNCTION public.user_role()          TO authenticated, service_role;" in self.sql

    def test_the_presence_patch_rpc_ships_with_it(self):
        assert "CREATE OR REPLACE FUNCTION public.patch_conv_presence" in self.sql
        assert "|| p_patch" in self.sql
        assert "set_config('app.skip_touch', '1', true)" in self.sql
        # CREATE OR REPLACE drops proconfig — it must re-declare its own.
        body = self.sql[self.sql.index("CREATE OR REPLACE FUNCTION public.patch_conv_presence"):]
        assert "SET search_path = 'public'" in body[:600]

    @pytest.mark.parametrize("fn", [
        "fn_update_timestamp()", "update_conv_presence(uuid, jsonb)",
        "fn_increment_message_count()", "fn_accumulate_ai_cost()",
        "fn_sync_lead_derived()", "log_message_delete()",
        "log_conversation_delete()", "user_role()", "user_tunnel_scope()",
    ])
    def test_every_named_function_pins_search_path(self, fn):
        assert f"ALTER FUNCTION public.{fn}" in self.sql

    def test_search_path_is_public_not_empty(self):
        """Trigger bodies use unqualified names — '' would break them at
        runtime, a worse outage than the risk being closed."""
        assert "SET search_path = 'public'" in self.sql
        assert "search_path = ''" not in self.sql

    def test_the_qm_stray_is_flagged_for_a_human(self):
        assert "barem_recent_events" in self.sql
        assert "likely DROP" in self.sql


# ════════════════════════════════════════════════════════════
# ROUTE TALK + THE DIANA RULE (prompt + gate)
# ════════════════════════════════════════════════════════════

class TestRouteAndFlowDiscipline:
    def _prompt(self) -> str:
        from app.ai.prompts import build_conversational_prompt
        from app.models.chat import VisitorInfo

        static, dynamic = build_conversational_prompt(
            tunnel="sales", visitor=VisitorInfo(name="Diana")
        )
        return f"{static}\n{dynamic}"

    def test_route_diminishing_is_banned_with_the_pensacola_example(self):
        p = self._prompt()
        assert "NEVER diminish a route" in p
        assert "Pensacola to Raleigh" in p

    def test_operational_invention_is_banned_by_name(self):
        p = self._prompt()
        for banned in ("connections", "hubs", "layovers", "aircraft types", "seat maps"):
            assert banned in p
        assert "you do not have live inventory" in p.lower()

    def test_diana_rule_present_with_its_example(self):
        p = self._prompt()
        assert "ALWAYS ends with the ONE next unanswered" in p
        assert "How many travelers?" in p

    @pytest.mark.parametrize("phrase", [
        "short hop", "quick flight", "short domestic", "easy trip",
        "connections through", "connecting through", "well-served",
    ])
    def test_the_coherence_gate_bans_them(self, phrase):
        from tests.test_voice_coherence import BANNED_PHRASES

        assert phrase in BANNED_PHRASES


# ════════════════════════════════════════════════════════════
# JOSEF — a callback request is never ambiguous
# ════════════════════════════════════════════════════════════

class TestCallbackIntent:
    @pytest.mark.parametrize("msg", [
        "call me", "Call me back please", "riing me on 210-555-0100",
        "ring me", "phone me", "can you call me", "give me a call",
        "+1 210 555 0100", "my number is 210-555-0100",
    ])
    def test_requests_are_detected(self, msg):
        from app.pipeline.callback_intent import detect_callback_request

        assert detect_callback_request(msg) is not None

    @pytest.mark.parametrize("msg", [
        "I'll call you tomorrow", "the airline called me yesterday",
        "don't call me", "I called you earlier",
        "I flew from Chicago to Miami last year and it was great",
    ])
    def test_non_requests_are_not(self, msg):
        from app.pipeline.callback_intent import detect_callback_request

        assert detect_callback_request(msg) is None

    def test_the_number_is_captured_and_normalized(self):
        from app.pipeline.callback_intent import detect_callback_request

        out = detect_callback_request("please call me on (210) 555-0100")
        assert out["number"] == "+12105550100"

    def test_the_note_leads_with_the_instruction(self):
        from app.pipeline.callback_intent import callback_note

        assert callback_note("+12105550100").startswith("CLIENT ASKED TO BE CALLED")

    @pytest.mark.asyncio
    async def test_e2e_josef_is_answered_queued_and_noted(self):
        from app.models.chat import VisitorInfo
        from app.pipeline.orchestrator import _pipeline

        conv = {"id": "c-josef", "mode": "ai", "status": "active",
                "tunnel": "sales", "metadata": {}}
        lead = {"id": "L-josef", "conversation_id": "c-josef", "notes": ""}
        with patch("app.services.conversation_service.get_or_create_conversation",
                   new=AsyncMock(return_value=conv)), \
             patch("app.services.conversation_service.add_message",
                   new=AsyncMock(return_value={"id": "m1"})), \
             patch("app.pipeline.orchestrator.db.get_recent_messages",
                   new=AsyncMock(return_value=[])), \
             patch("app.services.moderation.moderate_message", new=AsyncMock()), \
             patch("app.pipeline.orchestrator.db.update_conversation", new=AsyncMock()) as upd, \
             patch("app.pipeline.orchestrator.db.update_lead", new=AsyncMock()) as lead_upd, \
             patch("app.pipeline.orchestrator.lead_service.get_or_create_lead",
                   new=AsyncMock(return_value=lead)), \
             patch("app.services.routing.dispatch_needs_agent", new=AsyncMock()):
            resp = await _pipeline(
                "c-josef", "please riing me on 210-555-0100", "sales",
                VisitorInfo(name="Josef"), None,
                _persist_state={"ai_persisted": False},
            )

        # He is answered, not interrogated.
        assert "call you" in resp.message.lower()
        assert "+12105550100" in resp.message
        # The consultant sees the instruction first.
        notes = [c.args[1] for c in lead_upd.await_args_list]
        assert any("CLIENT ASKED TO BE CALLED" in (n.get("notes") or "") for n in notes)
        assert any(n.get("intent_signals", {}).get("callback_requested") for n in notes)
        # And a human is queued.
        assert any(c.args[1].get("status") == "needs_agent" for c in upd.await_args_list)


# ════════════════════════════════════════════════════════════
# INVITE PACK
# ════════════════════════════════════════════════════════════

class TestInvitePack:
    def test_links_survive_a_weekend(self):
        from config.settings import settings

        assert settings.invite_link_expiry_minutes == 2880   # 48h

    @pytest.mark.asyncio
    async def test_a_double_clicked_resend_reuses_the_fresh_token(self):
        """The real double-click path: Kate already exists (inactive) and
        the operator clicks Resend twice. A brand-new user cannot have a
        prior token, so no dedup check belongs on that branch."""
        from app.api import auth_routes
        from app.db import supabase as sb

        req = auth_routes.InviteRequest(name="Kate", email="kate@bbc.com")
        with patch.object(sb, "get_user_by_email", new=AsyncMock(return_value={
                 "id": "u1", "email": "kate@bbc.com", "is_active": False,
                 "role": "sales", "tunnel_scope": "sales"})), \
             patch.object(sb, "update_user", new=AsyncMock(return_value={"id": "u1"})), \
             patch.object(sb, "find_recent_invite_token",
                          new=AsyncMock(return_value={"token": "tok-existing"})), \
             patch.object(sb, "invalidate_active_invite_tokens", new=AsyncMock()) as inval, \
             patch.object(sb, "create_invite_token", new=AsyncMock()) as create:
            out = await auth_routes.invite_user(req, {"role": "owner", "id": "admin"})

        assert out["data"].get("deduplicated") is True
        create.assert_not_awaited()      # no twin token
        inval.assert_not_awaited()       # the one in her inbox survives

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reason", ["expired", "used", "superseded", "unknown"])
    async def test_each_failure_says_what_actually_happened(self, reason):
        from fastapi import HTTPException

        from app.api import auth_routes
        from app.db import supabase as sb

        req = auth_routes.SetPasswordRequest(token="t" * 24, password="hunter2hunter2")
        with patch.object(sb, "consume_valid_invite_token", new=AsyncMock(return_value=None)), \
             patch.object(sb, "diagnose_invite_token", new=AsyncMock(return_value=reason)):
            with pytest.raises(HTTPException) as exc:
                await auth_routes.set_password(req, None)
        assert exc.value.detail.startswith(f"{reason}:")
        assert exc.value.detail != f"{reason}: "

    @pytest.mark.asyncio
    async def test_re_request_is_throttled_and_never_leaks_accounts(self):
        from app.api import auth_routes
        from app.db import supabase as sb

        auth_routes._REINVITE_LAST.clear()
        req = auth_routes.RequestInviteRequest(email="ghost@nowhere.com")
        with patch.object(sb, "get_user_by_email", new=AsyncMock(return_value=None)):
            first = await auth_routes.request_invite(req, None)
        # Same answer for a non-existent account — no enumeration oracle.
        assert "pending invite" in first["data"]["message"]

        with patch.object(sb, "get_user_by_email", new=AsyncMock()) as lookup:
            second = await auth_routes.request_invite(req, None)
        assert second == first
        lookup.assert_not_awaited()      # throttled before touching the DB

    @pytest.mark.asyncio
    async def test_an_activated_account_is_never_re_invited(self):
        from app.api import auth_routes
        from app.db import supabase as sb

        auth_routes._REINVITE_LAST.clear()
        req = auth_routes.RequestInviteRequest(email="active@bbc.com")
        with patch.object(sb, "get_user_by_email",
                          new=AsyncMock(return_value={"id": "u1", "password_hash": "x"})), \
             patch.object(sb, "create_invite_token", new=AsyncMock()) as create:
            await auth_routes.request_invite(req, None)
        create.assert_not_awaited()


# ════════════════════════════════════════════════════════════
# PRESENCE TRUTH
# ════════════════════════════════════════════════════════════

class TestPresenceTruth:
    def _meta(self, presence, age_seconds, pings=True):
        seen = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
        return {
            "widget_presence": presence,
            "widget_last_event_at": seen.isoformat(),
            # Only a widget that actually pings may be aged down.
            **({"widget_pings": True} if pings else {}),
        }

    @pytest.mark.parametrize("age,expected", [
        (5, "online"), (60, "online"), (95, "stale"), (400, "stale"),
        (1900, "left"), (3600, "left"),
    ])
    def test_online_ages_down_never_up(self, age, expected):
        from app.db.supabase import derive_effective_presence

        state, seconds = derive_effective_presence(self._meta("online", age))
        assert state == expected
        assert seconds >= age - 2

    def test_explicit_states_keep_their_meaning(self):
        from app.db.supabase import derive_effective_presence

        assert derive_effective_presence(self._meta("minimized", 300))[0] == "minimized"
        assert derive_effective_presence(self._meta("left", 30))[0] == "left"

    def test_no_timestamp_leaves_the_stored_value_alone(self):
        from app.db.supabase import derive_effective_presence

        assert derive_effective_presence({"widget_presence": "online"}) == ("online", None)

    def test_both_read_paths_stamp_it(self):
        src = _read("app", "db", "supabase.py")
        assert src.count("attach_effective_presence(") >= 3   # def + list + detail

    def test_ping_patches_and_never_touches_updated_at(self):
        """029 discipline plus a rule the heartbeat forced: at 120 writes
        an hour a read-modify-write would eventually revert a claim flag
        another request set in between, and cost a whole conversation read
        every time."""
        src = _read("app", "api", "chat.py")
        ping = src[src.index("async def ping_chat_session"):]
        ping = ping[:ping.index("@router.post", 10)]
        assert "patch_conversation_presence" in ping
        assert "db.get_conversation(" not in ping          # no read at all
        assert "db.update_conversation(" not in ping
        assert "update_conversation_presence" not in ping  # no full-blob write

    def test_a_widget_that_never_pings_is_never_aged_down(self):
        """Rollout skew: clients on a cached older bundle send no pings.
        Ageing them down would paint the whole fleet 'left' on deploy."""
        from app.db.supabase import derive_effective_presence

        state, _age = derive_effective_presence(self._meta("online", 3600, pings=False))
        assert state == "online"

    def test_a_client_who_is_typing_is_present(self):
        """The message path only rewrites presence when it CHANGES, so a
        chatty client's ping timestamp can be minutes old mid-sentence."""
        from app.db.supabase import derive_effective_presence

        typed = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        state, age = derive_effective_presence(
            self._meta("online", 900), last_user_message_at=typed
        )
        assert state == "online" and age <= 12

    def test_naive_timestamps_do_not_blow_up_the_list(self):
        from app.db.supabase import derive_effective_presence

        naive = (datetime.now(timezone.utc) - timedelta(seconds=200)).replace(tzinfo=None)
        state, age = derive_effective_presence({
            "widget_presence": "online", "widget_pings": True,
            "widget_last_event_at": naive.isoformat(),
        })
        assert state == "stale" and age >= 190

    def test_clock_skew_never_reads_as_negative_or_stale(self):
        from app.db.supabase import derive_effective_presence

        future = (datetime.now(timezone.utc) + timedelta(seconds=45)).isoformat()
        state, age = derive_effective_presence({
            "widget_presence": "online", "widget_pings": True,
            "widget_last_event_at": future,
        })
        assert state == "online" and age == 0

    def test_derived_state_never_lands_back_in_metadata(self):
        """metadata is written back to the DB elsewhere — a read-time value
        living inside it would be persisted as if the client reported it."""
        from app.db.supabase import attach_effective_presence

        row = {"metadata": self._meta("online", 300), "id": "c1"}
        attach_effective_presence(row)
        assert row["client_presence"] == "stale"
        assert "widget_presence_effective" not in row["metadata"]

    def test_the_list_select_carries_what_the_derivation_needs(self):
        """The list REPLACES metadata with the lifted keys — without these
        three the derivation there was a guaranteed no-op."""
        src = _read("app", "db", "supabase.py")
        for key in ("widget_presence:metadata->>widget_presence",
                    "widget_last_event_at:metadata->>widget_last_event_at",
                    "widget_pings:metadata->>widget_pings"):
            assert key in src

    def test_the_live_presence_endpoint_derives_too(self):
        """It drives the line an operator reads before typing — if it
        alone stayed raw, the panel would contradict itself."""
        src = _read("app", "api", "conversations.py")
        assert "derive_effective_presence" in src
        assert "widget_presence_effective" in src

    def test_the_widget_pings_only_while_visible(self):
        src = _read("..", "bbc-widget", "src", "ChatWindow.tsx")
        assert "/ping" in src
        assert "visibilityState !== 'visible'" in src
        assert "visibilitychange" in src
        assert "clearInterval(pingInterval)" in src

    def test_widget_timers_are_untouched(self):
        """Owner-locked: this pack adds a ping, it does not retune
        auto-open/auto-close."""
        import subprocess

        diff = subprocess.run(
            ["git", "diff", "master", "--", "bbc-widget/src/Widget.tsx"],
            cwd=os.path.join(ROOT, ".."), capture_output=True, text=True, timeout=60,
        )
        assert diff.stdout.strip() == ""
