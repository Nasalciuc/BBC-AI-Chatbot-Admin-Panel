"""CodeRabbit #228, full review (9 nov 2026), checked against the code.

Each section is one finding: what the code did, and the path that must not
change (rule 5)."""

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.api.auth_routes import _login_place  # noqa: E402


# ── the login place: one network, one key, whatever its spelling ───────────

def _req(headers=None):
    return SimpleNamespace(headers=headers or {})


@pytest.mark.parametrize("ip", ["2001:db8::5", "2001:0db8:0:1::5", "2001:db8:0000:ffff::1", "2001:DB8::abcd"])
def test_one_ipv6_48_is_one_place(ip):
    # Cut from the text these gave "2001:db8:", "2001:0db8:0" … : a new place
    # each, and a "new place" alert for the same person on the same network.
    assert _login_place(_req(), ip) == "net:2001:db8::/48"


def test_ipv4_is_its_24_and_a_mapped_address_is_the_same_place():
    assert _login_place(_req(), "203.0.113.9") == "net:203.0.113.0/24"
    assert _login_place(_req(), "203.0.113.200") == "net:203.0.113.0/24"
    assert _login_place(_req(), "::ffff:203.0.113.9") == "net:203.0.113.0/24"


@pytest.mark.parametrize("ip", ["1.2.3.x", "999.1.1.1", "not-an-ip", "", "unknown"])
def test_what_is_not_an_address_is_no_place(ip):
    assert _login_place(_req(), ip) == ""


def test_the_edges_country_still_wins():
    assert _login_place(_req({"cf-ipcountry": "ro"}), "203.0.113.9") == "country:RO"
    assert _login_place(_req({"cf-ipcountry": "XX"}), "203.0.113.9") == "net:203.0.113.0/24"
