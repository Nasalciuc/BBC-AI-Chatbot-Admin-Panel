"""Every chat says where the visit came from, and the CRM sees it (owner, 23 Nov 2026; point D of "Conversia în chat").

On 9-23 Nov 37 chats reached the server with no source at all (15 of them with a contact) and 7 with an ad's click id but
no campaign tag, whose CRM leads then had no source. The widget now fills a chat whose tab kept nothing from the visit's
PostHog session (bbc-widget utm.ts sessionSource, marked `source_via`), and the CRM's source falls back to the ad's click
id. The contacts and the ids below are invented.
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.security.metadata import allowed_widget_metadata
from app.services.crm import build_crm_payload, compute_defaults_notes, crm_source

_LEAD = {"origin_code": "JFK", "destination_code": "LHR", "departure_date": "2027-02-10", "trip_type": "one_way",
         "passengers": 1, "cabin_class": "business"}
_VISITOR = SimpleNamespace(name="Test Client", email="client@example.com", phone="", country_code=None)


# ── the widget's word reaches the conversation ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("via", ["posthog", "direct"])
def test_the_widget_says_how_it_knows_where_the_visit_came_from(via):
    raw = {"utm_source": "fb", "fbclid": "f-1", "referrer": "https://l.facebook.com/", "source_via": via}
    assert allowed_widget_metadata(raw) == raw


@pytest.mark.parametrize("bad", ["PostHog", "posthog ", "google", "", None, 1, ["posthog"]])
def test_only_its_two_words_are_kept(bad):
    assert "source_via" not in allowed_widget_metadata({"page_url": "https://example.com/", "source_via": bad})


# ── the CRM's source ─────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("meta, source", [
    ({"gclid": "g-1"}, "google"),
    ({"msclkid": "m-1"}, "bing"),
    ({"fbclid": "f-1"}, "fb"),
    ({"kayak_click_id": "k-1"}, "kayak"),
])
def test_an_ad_click_without_its_tag_still_names_its_source(meta, source):
    assert crm_source(meta) == source
    assert build_crm_payload(_LEAD, _VISITOR, conv_metadata=meta)["_utmsource"] == source


def test_the_campaigns_own_tag_wins_as_before():
    meta = {"utm_source": "newsletter", "utm_medium": "email", "gclid": "g-1"}
    payload = build_crm_payload(_LEAD, _VISITOR, conv_metadata=meta)
    assert payload["_utmsource"] == "newsletter" and payload["_utmmedium"] == "email" and payload["gclid"] == "g-1"


def test_bings_click_id_rides_like_googles():
    payload = build_crm_payload(_LEAD, _VISITOR, conv_metadata={"utm_source": "bing", "msclkid": "m-1"})
    assert payload["msclkid"] == "m-1" and payload["_utmsource"] == "bing"


@pytest.mark.parametrize("meta", [{}, None, {"source_via": "direct"}, {"page_url": "https://example.com/", "utm_source": "  "}])
def test_nothing_known_sends_no_source(meta):
    # The path that takes no fallback: a direct visit, or nothing at all, sends no campaign source.
    payload = build_crm_payload(_LEAD, _VISITOR, conv_metadata=meta)
    assert "_utmsource" not in payload and "msclkid" not in payload


def test_the_consultants_note_names_the_same_source():
    thin = {**_LEAD, "departure_date": None}
    note = compute_defaults_notes(thin, {"msclkid": "m-1", "page_url": "https://example.com/rome"})[0]
    assert "source: bing" in note
    assert "source: direct" in compute_defaults_notes(thin, {})[0]
