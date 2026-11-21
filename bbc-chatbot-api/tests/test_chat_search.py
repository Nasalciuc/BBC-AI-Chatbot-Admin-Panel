"""The search box: what was typed decides where to look, and nothing typed can add a condition (hole A).

The filters are read the way PostgREST reads them (tests/postgrest_eval.py), so the tests hold them to
rows, not to their spelling. Every name, e-mail and phone here is invented.
"""

import asyncio
import itertools
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.db import supabase as db  # noqa: E402
from app.services import chat_search as cs  # noqa: E402
from tests.postgrest_eval import holds, like, split_top, truth, unquote  # noqa: E402

ME = "aaaaaaaa-0000-4000-8000-000000000001"


# ── what was typed ───────────────────────────────────────────────────


@pytest.mark.parametrize("typed, kind, value", [
    ("3403", "number", "3403"),
    ("#3403", "number", "3403"),
    ("  #0042 ", "number", "42"),
    ("Grace@Example.com", "email", "grace@example.com"),
    ("grace@", "email", "grace@"),
    ("+1 (214) 208-5185", "phone", "12142085185"),
    ("214.208.5185", "phone", "2142085185"),
    ("2142085", "phone", "2142085"),
    ("IST", "airport", "IST"),
    ("Istanbul", "text", "Istanbul"),
    ("ist", "text", "ist"),
    ("+97150", "text", "+97150"),
    ("1234567890123456789", "phone", "1234567890123456789"),
])
def test_what_was_typed_decides_where_to_look(typed, kind, value):
    assert cs.parse(typed) == cs.Query(kind, value)


@pytest.mark.parametrize("typed", ["٣٤٥", "１２３", "１２３４５６７"])
def test_digits_other_than_ascii_are_just_text(typed):
    """Rule 5: str.isdigit() says yes to these; a chat number or a phone never holds them."""
    assert cs.parse(typed).kind == "text"


def test_nothing_typed_is_no_search():
    assert cs.parse(None) is None and cs.parse("   ") is None


# ── the value is one value: PostgREST's quoting ───────────────────────


NASTY = [
    "a%,visitor_email.ilike.%gmail",            # the hole as found on 21 Nov
    "x),or(id.not.is.null",
    '"),visitor_email.ilike.(%',
    'quote " and \\ backslash',
    "trailing backslash \\",
    "{a,b}",
    "a.b.c:d",
    "100%_done",
    "*",
    "not.or(id.not.is.null)",
]


@pytest.mark.parametrize("text", NASTY)
def test_a_quoted_value_reads_back_as_itself(text):
    raw = cs.quoted(text)
    assert split_top(raw) == [raw], "a quoted value is never split"
    assert unquote(raw) == text


@pytest.mark.parametrize("text", NASTY + ["Grace Miller", "o'neil"])
def test_contains_matches_the_text_and_nothing_wider(text):
    cond = cs.contains("visitor_name", text)
    assert holds(cond, {"visitor_name": f"before {text} after"})
    assert holds(cond, {"visitor_name": text.upper()}) or text.upper() == text.lower() or "*" in text
    if text != "*":
        assert not holds(cond, {"visitor_name": "something else entirely"})


def test_like_wildcards_typed_by_the_client_match_themselves():
    assert like(cs.contains_pattern("100%"), "it is 100% done", case=False)
    assert not like(cs.contains_pattern("100%"), "it is 1000 done", case=False)
    assert like(cs.contains_pattern("a_b"), "xa_bx", case=False)
    assert not like(cs.contains_pattern("a_b"), "xacbx", case=False)
    assert like(cs.contains_pattern("back\\slash"), "a back\\slash b", case=False)


def test_a_star_still_finds_the_star_and_adds_no_condition():
    """PostgREST turns every * into %: the client's * becomes _ (any one character)."""
    assert like(cs.contains_pattern("a*b"), "xa*bx", case=False)
    assert like(cs.contains_pattern("a*b"), "xacbx", case=False)
    assert not like(cs.contains_pattern("a*b"), "xab", case=False)


# ── the list's search, built and read back ───────────────────────────


class _Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if name == "not_":  # a property in postgrest-py: the next filter is negated
            self.calls.append(("not_", (), {}))
            return self

        def rec(*a, **k):
            self.calls.append((name, a, k))
            return self
        return rec

    def execute(self):
        res = MagicMock()
        res.data, res.count = [], 0
        return res


def _built(viewer, term, *, route_ids=None):
    q = _Recorder()
    client = MagicMock()
    client.table.return_value = q
    with patch.object(db, "get_client", return_value=client), \
            patch.object(db, "_supervisor_columns_available", return_value=True), \
            patch.object(db, "_get_lead_completeness_inputs", new=AsyncMock(return_value={})), \
            patch.object(db, "enrich_conversations_agent_info", new=AsyncMock(side_effect=lambda r: r)):
        asyncio.run(db.get_conversations(search=term, blind_viewer=viewer, search_route_ids=route_ids))
    return q.calls


def _search_or(calls):
    return [f"or({c[1][0]})" for c in calls if c[0] == "or_" and not c[2].get("reference_table")]


WAITING = {"id": "c-waiting", "status": "needs_agent", "mode": "ai", "assigned_agent_id": None,
           "queued_at": "2026-11-20T20:00:00+00:00", "visitor_name": "Grace Miller",
           "visitor_email": "grace.miller@gmail.com", "visitor_phone": "+1 (214) 208-5185",
           "metadata": {"engaged_agent_id": None}}
MINE = dict(WAITING, id="c-mine", status="active", mode="human", assigned_agent_id=ME, queued_at=None)


@pytest.mark.parametrize("attack", [
    "a%,visitor_email.ilike.%gmail",
    "zz),visitor_email.ilike.(%gmail",
    'zz",visitor_email.ilike."%gmail',
    "zz\\,visitor_email.ilike.%gmail",
    "@x%,visitor_email.ilike.%gmail",          # reads as an e-mail: guarded, and still one value
])
def test_hole_a_typing_a_condition_finds_nothing_more(attack):
    """The permanent test of hole A: an agent's search that spells a filter gets no waiting chat by its
    e-mail, and every chat it does match holds the typed text itself."""
    (expr,) = _search_or(_built(ME, attack))
    assert not holds(expr, WAITING), "the typed condition opened the blind guard"
    assert not holds(expr, MINE), "the typed condition matched a chat that doesn't hold the text"
    inner = split_top(expr[3:-1])
    assert len(inner) <= 3, f"the text added a condition: {inner}"


@pytest.mark.parametrize("term", NASTY)
def test_the_shape_of_the_filter_never_depends_on_the_text(term):
    """Whatever is typed, the same conditions at the top. A text: for an agent the name, the guarded
    contact and the guarded routes; for a manager the name, the e-mail, the phone and the routes."""
    for viewer, text_parts in ((ME, 3), (None, 4)):
        (expr,) = _search_or(_built(viewer, term, route_ids=["aaaaaaaa-0000-4000-8000-0000000000aa"]))
        expected = {"text": text_parts, "email": 1, "phone": 1}[cs.parse(term).kind]
        assert len(split_top(expr[3:-1])) == expected, (viewer, term, expr)


def test_a_phone_is_found_by_its_digits_whatever_the_format():
    for typed in ("2142085185", "+1 214-208-5185", "(214) 208 5185"):
        (expr,) = _search_or(_built(None, typed))
        assert holds(expr, dict(WAITING, visitor_phone_digits="12142085185")), typed


def test_without_054_the_phone_regex_gives_the_same_answers(monkeypatch):
    """The fallback (no visitor_phone_digits): the digits in a row, only separators between them."""
    phones = ["+1 (214) 208-5185", "2142085185", "+44 20 2142 0851 85", "214-208-51-85", "2149208518", None]
    for digits in ("2142085185", "4208", "1214208"):
        regex = cs.phone_condition(digits, digits_column=False)
        field = cs.phone_condition(digits, digits_column=True)
        for phone in phones:
            row = {"visitor_phone": phone,
                   "visitor_phone_digits": "".join(ch for ch in phone if ch.isdigit()) if phone else None}
            assert holds(regex, row) == holds(field, row), (digits, phone)


def test_a_waiting_chat_is_found_by_its_phone_only_once_readable():
    (expr,) = _search_or(_built(ME, "+1 214 208 5185"))
    assert not holds(expr, dict(WAITING, visitor_phone_digits="12142085185"))
    assert holds(expr, dict(MINE, visitor_phone_digits="12142085185"))


def test_a_city_finds_the_chats_of_its_routes_and_guards_them_for_an_agent():
    routed = "aaaaaaaa-0000-4000-8000-0000000000aa"
    (expr,) = _search_or(_built(ME, "Istanbul", route_ids=[routed]))
    assert holds(expr, dict(MINE, id=routed, visitor_name="Someone Else"))
    assert not holds(expr, dict(WAITING, id=routed, visitor_name="Someone Else")), "the trip is blind too"
    (expr,) = _search_or(_built(None, "Istanbul", route_ids=[routed]))
    assert holds(expr, dict(WAITING, id=routed, visitor_name="Someone Else"))


def test_a_name_search_without_routes_adds_no_route_condition():
    """Rule 5: no route ids (none found, or the read failed) means the name and the contact only."""
    (expr,) = _search_or(_built(ME, "Istanbul", route_ids=[]))
    assert len(split_top(expr[3:-1])) == 2


def test_an_airport_is_a_condition_on_the_lead_and_guarded_for_an_agent():
    calls = _built(ME, "IST")
    on_lead = [c for c in calls if c[0] == "or_" and c[2].get("reference_table") == "leads"]
    assert on_lead and holds(f"or({on_lead[0][1][0]})", {"origin_code": "IST", "destination_code": "JFK"})
    assert not holds(f"or({on_lead[0][1][0]})", {"origin_code": "JFK", "destination_code": "LHR"})
    selects = [c for c in calls if c[0] == "select"]
    assert "leads!inner(id)" in selects[0][1][0]
    (guard,) = _search_or(calls)
    assert not holds(guard, WAITING) and holds(guard, MINE)


def test_a_chat_number_is_the_number_for_everyone():
    (expr,) = _search_or(_built(ME, "#3403"))
    assert holds(expr, dict(WAITING, chat_number=3403)) and not holds(expr, dict(WAITING, chat_number=34030))


# ── where the client came from: three chips, every chat in exactly one ──────


def _metas():
    sources = [None, "", "kayak", " Kayak ", "SKYSCANNER", "google", "Google", "bing", "fb", "newsletter"]
    mediums = [None, "cpc", "CPC", "paid_social", "email", ""]
    clicks = [{}, {"gclid": "g1"}, {"msclkid": "m1"}, {"fbclid": "f1"}, {"kayak_click_id": "k1"},
              {"gclid": ""}, {"kclid": "k2"}]
    for source, medium, click in itertools.product(sources, mediums, clicks):
        meta = dict(click)
        if source is not None:
            meta["utm_source"] = source
        if medium is not None:
            meta["utm_medium"] = medium
        yield meta
    yield None  # no metadata at all


def _chip(source, row):
    return all(truth(f"or({c})", row) is True for c in cs.source_conditions(source))


def _expected(meta):
    from app.pipeline.entity_extractor import COMPARISON_CLICK_IDS, COMPARISON_SOURCES
    m = meta or {}
    if (m.get("utm_source") or "").strip().lower() in COMPARISON_SOURCES or any(m.get(k) for k in COMPARISON_CLICK_IDS):
        return "kayak"
    if any(m.get(k) for k in cs.PAID_CLICK_IDS) or (m.get("utm_medium") or "").strip().lower() in cs.PAID_MEDIUMS:
        return "ads"
    return "site"


def test_every_chat_is_in_exactly_the_chip_python_would_put_it_in():
    for meta in _metas():
        row = {"metadata": meta}
        got = [s for s in cs.SOURCES if _chip(s, row)]
        assert got == [_expected(meta)], meta


def test_kayak_paid_by_cpc_is_kayak_not_an_ad():
    """Production, 30 days to 21 Nov: 154 Kayak chats, every one utm_medium=cpc."""
    row = {"metadata": {"utm_source": "kayak", "utm_medium": "cpc"}}
    assert _chip("kayak", row) and not _chip("ads", row) and not _chip("site", row)


def test_an_unknown_source_is_refused():
    with pytest.raises(ValueError):
        cs.source_conditions("tv")


# ── the other or=() fed by typed or visitor text ───────────────────────


def _recorded(call):
    q = _Recorder()
    client = MagicMock()
    client.table.return_value = q
    with patch.object(db, "get_client", return_value=client), \
            patch.object(db, "_chat_enabled_column_available", return_value=False):
        asyncio.run(call())
    return [f"or({c[1][0]})" for c in q.calls if c[0] == "or_"]


def test_the_users_search_is_one_value():
    (expr,) = _recorded(lambda: db.get_users(search="Smith, John"))
    assert holds(expr, {"name": "Ana Smith, John", "email": "ana@example.com"})
    assert not holds(expr, {"name": "Smith", "email": "john@example.com"}), "the comma started a second search"


def test_the_users_search_without_reserved_characters_finds_what_it_did():
    """Rule 5: a plain search reads the same as before the quoting."""
    (expr,) = _recorded(lambda: db.get_users(search="ana"))
    assert holds(expr, {"name": "Ana Smith", "email": None}) and holds(expr, {"name": None, "email": "x.ana@example.com"})
    assert not holds(expr, {"name": "Bob", "email": "bob@example.com"})


def test_a_returning_visitors_phone_with_parentheses_is_one_value():
    (expr,) = _recorded(lambda: db.get_last_agent_for_visitor("dana@example.com", "+1 (214) 208-5185"))
    assert holds(expr, {"visitor_email": None, "visitor_phone": "+1 (214) 208-5185"})
    assert holds(expr, {"visitor_email": "dana@example.com", "visitor_phone": None})
    assert not holds(expr, {"visitor_email": "other@example.com", "visitor_phone": "+1 (214) 208-5186"})
