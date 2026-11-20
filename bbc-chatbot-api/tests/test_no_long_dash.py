"""The long dash (20 Nov 2026, the owner: "se spală că e AI"): no reply the client reads has "—" in it.

The model wrote "No problem at all — your consultant will stick to email", copying the prompt's own example,
and the fixed words had it too. Every stored AI reply now has a comma there (output_guard.no_long_dash, in
db.add_message through guard_ai_text), and so does every streamed piece (StreamGuard). The prompt's examples
lost it and a rule says why. The en dash of a date range is another character and stays.
"""

import os
import random
import re
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.ai import output_guard, prompts
from app.ai.output_guard import StreamGuard, guard_ai_text, no_long_dash

SEEN = ("No problem at all — your consultant will stick to email. Whenever you're ready, your dedicated "
        "consultant will have the private options waiting in your inbox — first thing when the team is back.")
SAID = ("No problem at all, your consultant will stick to email. Whenever you're ready, your dedicated "
        "consultant will have the private options waiting in your inbox, first thing when the team is back.")


def test_the_reply_the_owner_saw_reads_with_commas():
    assert no_long_dash(SEEN) == SAID


@pytest.mark.parametrize("text, said", [
    ("Fair question — we're IATA accredited.", "Fair question, we're IATA accredited."),
    ("word—word", "word, word"),
    ("Two quick things —", "Two quick things"),                 # the end of the reply: nothing in its place
    ("Done. — An agent writes.", "Done. An agent writes."),     # after a full stop: no comma
    ("Hi, — there", "Hi, there"),                               # never two commas
    ("— a line\n— another", "a line\nanother"),                 # the start of a line
    ("it — (maybe) — yes", "it, (maybe), yes"),
    ("📅 2026-12-10 — 2026-12-17 (round trip)", "📅 2026-12-10 – 2026-12-17 (round trip)"),  # a range keeps a range's dash
    ("2—3 days", "2–3 days"),
    ("Dec 30 – Jan 6", "Dec 30 – Jan 6"),                       # the en dash is not this dash
])
def test_the_long_dash_and_what_is_said_instead(text, said):
    assert no_long_dash(text) == said


def test_a_reply_without_it_is_the_same_text():
    text = "Where are you flying from and to?"
    assert no_long_dash(text) is text
    assert no_long_dash("") == "" and no_long_dash(None) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["log", "enforce"])
async def test_every_stored_reply_loses_it_in_every_mode(monkeypatch, mode):
    monkeypatch.setenv("OUTPUT_GUARD_MODE", mode)
    assert await guard_ai_text(SEEN) == SAID


@pytest.mark.parametrize("mode", ["log", "enforce"])
def test_a_streamed_reply_reads_as_the_stored_one_however_it_is_cut(monkeypatch, mode):
    """A chunk may end on " —" or start on "— ": the piece's trailing spaces and dash wait for the next one."""
    monkeypatch.setenv("OUTPUT_GUARD_MODE", mode)
    for seed in range(200):
        rnd = random.Random(seed)
        cuts = sorted(rnd.sample(range(1, len(SEEN)), 15))
        parts = [SEEN[a:b] for a, b in zip([0, *cuts], [*cuts, len(SEEN)])]
        g = StreamGuard()
        streamed = "".join(g.feed(p) for p in parts) + g.flush()
        assert streamed == SAID, (seed, parts)


@pytest.mark.parametrize("parts", [
    ["No problem at all ", "— your consultant will stick to email."],
    ["No problem at all —", " your consultant will stick to email."],
    ["No problem at all", " — ", "your consultant will stick to email."],
])
def test_the_dash_split_between_two_chunks_is_still_one_comma(monkeypatch, parts):
    monkeypatch.setenv("OUTPUT_GUARD_MODE", "log")
    g = StreamGuard()
    out = [g.feed(p) for p in parts]
    assert "".join(out) + g.flush() == "No problem at all, your consultant will stick to email."
    assert not any("—" in piece for piece in out)


@pytest.mark.parametrize("parts, said", [
    (["Done. ", "— An agent writes."], "Done. An agent writes."),     # CodeRabbit #272: no ". ,"
    (["Hi,", " — there"], "Hi, there"),                             # …and no ",,"
    (["Hi,", "— there"], "Hi, there"),
    (["Done.", " — An agent writes."], "Done. An agent writes."),
    (["a line\n", "— another"], "a line\nanother"),                 # a line's start, in the next piece
    (["2", "—3 days"], "2–3 days"),                                  # a range across the cut
])
def test_a_piece_is_read_after_what_went_out_before_it(monkeypatch, parts, said):
    monkeypatch.setenv("OUTPUT_GUARD_MODE", "log")
    g = StreamGuard()
    assert "".join(g.feed(p) for p in parts) + g.flush() == said == no_long_dash("".join(parts))


_CUT_ANYWHERE = [
    SEEN, "Done. — An agent writes.", "Hi, — there", "— a line\n— another", "it — (maybe) — yes",
    "Two quick things —", "Fair question — we're IATA accredited.", "Sure!  — I can help — today.",
]


@pytest.mark.parametrize("mode", ["log", "enforce"])
@pytest.mark.parametrize("text", _CUT_ANYWHERE)
def test_cut_anywhere_the_stream_says_what_the_stored_reply_says(monkeypatch, mode, text):
    monkeypatch.setenv("OUTPUT_GUARD_MODE", mode)
    for seed in range(120):
        rnd = random.Random(seed)
        n = min(len(text) - 1, rnd.randint(1, 8))
        cuts = sorted(rnd.sample(range(1, len(text)), n))
        parts = [text[a:b] for a, b in zip([0, *cuts], [*cuts, len(text)])]
        g = StreamGuard()
        out = [g.feed(p) for p in parts] + [g.flush()]
        assert "".join(out) == no_long_dash(text), (seed, parts)
        assert not any("—" in piece for piece in out)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["log", "enforce"])
async def test_the_line_said_in_place_of_a_price_has_none_either(monkeypatch, mode):
    """CodeRabbit #272: the price check puts its own line in the reply, after the dash was taken out."""
    monkeypatch.setenv("OUTPUT_GUARD_MODE", mode)
    out = await guard_ai_text("It costs $3,200.")
    assert out == output_guard.PRICE_LINE and "—" not in out and "3,200" not in out


def test_a_dash_that_starts_a_mid_line_chunk_is_a_pause_not_a_line_start(monkeypatch):
    monkeypatch.setenv("OUTPUT_GUARD_MODE", "log")
    g = StreamGuard()
    assert g.feed("in your inbox") + g.feed("— first thing") + g.flush() == "in your inbox, first thing"


def test_the_prompt_says_so_and_its_examples_show_it():
    text = prompts.COMMON_RULES + prompts.SALES_INSTRUCTIONS
    assert "NEVER write the long dash (—) to the client" in text
    assert '"No problem at all, your consultant will stick to email."' in text
    said = [q for line in text.splitlines() if line.lstrip().startswith(("You", "Capture:", "Close:"))
            for q in re.findall(r'"([^"\n]*)"', line)]
    assert said and not [q for q in said if "—" in q]


def test_the_system_lines_the_client_reads_have_none():
    from app.services.handoff import _FALLBACK_MSG
    from config.settings import settings

    assert "—" not in _FALLBACK_MSG
    assert "—" not in settings.heartbeat_joined_template.format(agent_name="Agent")
    assert output_guard.no_long_dash(_FALLBACK_MSG) == _FALLBACK_MSG
