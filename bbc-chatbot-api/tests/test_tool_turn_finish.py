"""A reply that stopped at the tool call is finished after the tool's answer.

stop_reason "tool_use" means the model waits for the tool's answer and writes
its reply after it. The reply used to end at the tool call: on 16 Nov 2026 a
client got "I'll save those details for you." and nothing else, and about
half of the turns that saved details had no words at all (Railway, 1-16 Nov).
Now the tool's answer goes back and the model finishes the reply, streamed
to the client after the words already sent; it cannot call the tool again.
"""

import json
import os
import sys
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import anthropic
import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")

from app.ai import claude  # noqa: E402

MODEL = "claude-opus-4-8"
SYSTEM = ("static rules", "dynamic context")
PREAMBLE = "I'll save those details for you."
REST = "Thanks! What is the best email to send your options to?"
ROUTE = {"origin": "JFK", "destination": "LHR"}


# ── fakes ───────────────────────────────────────────────────────────────────

def _usage(out=5):
    return SimpleNamespace(input_tokens=10, output_tokens=out,
                           cache_creation_input_tokens=0, cache_read_input_tokens=0)


def _text(words):
    return SimpleNamespace(type="text", text=words)


def _tool(entities, tool_id="toolu_01"):
    return SimpleNamespace(type="tool_use", id=tool_id, name="save_travel_details", input=entities)


def _message(*blocks, stop="end_turn", out=5):
    return SimpleNamespace(content=list(blocks), stop_reason=stop, usage=_usage(out), id="msg_x")


class _Stream:
    def __init__(self, chunks, final, broken=None):
        self._chunks, self._final, self._broken = chunks, final, broken

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        for chunk in self._chunks:
            yield chunk
        if self._broken is not None:
            raise self._broken

    def get_final_message(self):
        return self._final


def _client(streams=(), creates=()):
    client = MagicMock()
    client.messages.stream.side_effect = list(streams)
    client.messages.create.side_effect = list(creates)
    return client


def _stream_turn(client, chunks_out):
    with patch.object(claude, "_get_client", return_value=client):
        return claude._stream_model_with_tools(
            MODEL, SYSTEM, "jfk to london please", max_tokens=450, temperature=0.6,
            on_chunk=chunks_out.append, effort="medium",
        )


# ── streamed path ─────────────────────────────────────────────────────────────

class TestStreamedTurn:
    def test_the_line_before_the_tool_is_followed_by_the_reply(self):
        client = _client(streams=[
            _Stream([PREAMBLE], _message(_text(PREAMBLE), _tool(ROUTE), stop="tool_use")),
            _Stream(["Thanks! What is the best ", "email to send your options to?"],
                    _message(_text(REST), out=20)),
        ])
        sent: list[str] = []
        text, cost, entities = _stream_turn(client, sent)

        assert text == f"{PREAMBLE} {REST}"
        # What the client watched arrive: the line, one space, then the rest.
        assert "".join(sent) == f"{PREAMBLE} {REST}"
        assert sent[1] == " "
        assert entities == ROUTE
        assert client.messages.stream.call_count == 2
        assert cost == claude.cost_from_usage(MODEL, _usage()) + claude.cost_from_usage(MODEL, _usage(20))

    def test_the_second_call_carries_the_turn_and_the_tools_answer_and_no_second_tool_call(self):
        client = _client(streams=[
            _Stream([PREAMBLE], _message(_text(PREAMBLE), _tool(ROUTE), stop="tool_use")),
            _Stream([REST], _message(_text(REST))),
        ])
        _stream_turn(client, [])
        first, second = (c.kwargs for c in client.messages.stream.call_args_list)

        assert first["model"] == MODEL
        # The same model, room, system, tools and settings; only the
        # conversation grows, and the tool can no longer be called.
        same = {k: v for k, v in first.items() if k != "messages"}
        assert {k: v for k, v in second.items() if k not in ("messages", "tool_choice")} == same
        assert second["tool_choice"] == {"type": "none"}
        assert "tool_choice" not in first
        assert second["messages"] == [
            {"role": "user", "content": "jfk to london please"},
            {"role": "assistant", "content": [
                {"type": "text", "text": PREAMBLE},
                {"type": "tool_use", "id": "toolu_01", "name": "save_travel_details", "input": ROUTE},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_01", "content": claude._TOOL_SAVED},
            ]},
        ]

    def test_no_words_before_the_tool_the_reply_is_the_rest_alone(self):
        """The 0-character turns: the reply comes from the rest, with no space
        in front, and the plain re-call without the tool is no longer needed."""
        client = _client(streams=[
            _Stream([], _message(_tool(ROUTE), stop="tool_use")),
            _Stream(["  ", "\n", REST], _message(_text(REST))),
        ])
        sent: list[str] = []
        text, _cost, entities = _stream_turn(client, sent)
        assert text == REST
        assert "".join(sent) == REST
        assert entities == ROUTE
        # Words that were empty are left out of the turn sent back.
        turn = client.messages.stream.call_args_list[1].kwargs["messages"][1]["content"]
        assert [b["type"] for b in turn] == ["tool_use"]

    def test_a_turn_that_did_not_stop_at_the_tool_is_left_as_it_is(self):
        reply = "Business class to London — when would you like to fly?"
        client = _client(streams=[_Stream([reply], _message(_text(reply)))])
        sent: list[str] = []
        text, _cost, entities = _stream_turn(client, sent)
        assert (text, entities, sent) == (reply, {}, [reply])
        assert client.messages.stream.call_count == 1

    def test_nothing_more_to_say_keeps_the_words_before_the_tool(self):
        full = "Great choice, JFK to London in business. What is the best email for you?"
        client = _client(streams=[
            _Stream([full], _message(_text(full), _tool(ROUTE), stop="tool_use")),
            _Stream([], _message(stop="end_turn", out=2)),
        ])
        sent: list[str] = []
        text, _cost, _entities = _stream_turn(client, sent)
        assert text == full
        assert sent == [full]  # no stray space when nothing follows

    def test_a_rest_that_fails_keeps_the_reply_and_the_saved_details(self, caplog):
        client = _client(streams=[
            _Stream([PREAMBLE], _message(_text(PREAMBLE), _tool(ROUTE), stop="tool_use")),
            anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.invalid")),
        ])
        with caplog.at_level("WARNING"):
            text, cost, entities = _stream_turn(client, [])
        assert text == PREAMBLE and entities == ROUTE
        assert cost == claude.cost_from_usage(MODEL, _usage())
        said = [r.getMessage() for r in caplog.records if "rest of the reply failed" in r.getMessage()]
        assert said == ["Claude+Tool STREAM rest of the reply failed (APITimeoutError): keeping the words before the tool call"]

    def test_a_rest_that_breaks_halfway_is_not_kept_cut_short(self):
        """What it sent is replaced at stream_end by the stored reply."""
        client = _client(streams=[
            _Stream([PREAMBLE], _message(_text(PREAMBLE), _tool(ROUTE), stop="tool_use")),
            _Stream(["Thanks! What is"], None, broken=RuntimeError("connection reset")),
        ])
        text, _cost, entities = _stream_turn(client, [])
        assert text == PREAMBLE and entities == ROUTE

    def test_the_log_line_says_the_reply_went_on_and_never_its_words(self, caplog):
        client = _client(streams=[
            _Stream([PREAMBLE], _message(_text(PREAMBLE), _tool(ROUTE), stop="tool_use")),
            _Stream([REST], _message(_text(REST))),
        ])
        with caplog.at_level("INFO"):
            _stream_turn(client, [])
        line = next(r.getMessage() for r in caplog.records if "STREAM SUCCESS" in r.getMessage())
        assert f"text={len(PREAMBLE) + 1 + len(REST)}ch" in line and f"after_tool={len(REST)}ch" in line
        everything = "\n".join(r.getMessage() for r in caplog.records)
        assert "email to send" not in everything and "save those details" not in everything


# ── the turn sent back ────────────────────────────────────────────────────────

class TestTheTurnSentBack:
    def test_thinking_goes_back_whole_and_every_tool_call_gets_an_answer(self):
        response = _message(
            SimpleNamespace(type="thinking", thinking="they want JFK-LHR", signature="sig=="),
            SimpleNamespace(type="redacted_thinking", data="opaque"),
            _text(PREAMBLE),
            _tool(ROUTE, "toolu_a"),
            _tool({"cabin_class": "business"}, "toolu_b"),
            stop="tool_use",
        )
        messages = claude._after_tool_messages("jfk lhr", response)
        assert messages[1]["content"] == [
            {"type": "thinking", "thinking": "they want JFK-LHR", "signature": "sig=="},
            {"type": "redacted_thinking", "data": "opaque"},
            {"type": "text", "text": PREAMBLE},
            {"type": "tool_use", "id": "toolu_a", "name": "save_travel_details", "input": ROUTE},
            {"type": "tool_use", "id": "toolu_b", "name": "save_travel_details", "input": {"cabin_class": "business"}},
        ]
        assert [b["tool_use_id"] for b in messages[2]["content"]] == ["toolu_a", "toolu_b"]

    def test_only_a_turn_that_stopped_at_the_tool_goes_on(self):
        assert claude._after_tool_messages("hi", _message(_text("Hello!"))) is None
        assert claude._after_tool_messages("hi", _message(_text("Hi"), _tool(ROUTE), stop="max_tokens")) is None
        # A MagicMock response (the older tests' fakes) has no real stop_reason.
        assert claude._after_tool_messages("hi", MagicMock()) is None

    def test_an_empty_client_message_keeps_the_payload_valid(self):
        messages = claude._after_tool_messages("", _message(_tool(ROUTE), stop="tool_use"))
        assert messages[0] == {"role": "user", "content": claude._EMPTY_USER_STUB}

    def test_join(self):
        assert claude._join_reply(" I'll note that. ", "\nWhen do you fly? ") == "I'll note that. When do you fly?"
        assert claude._join_reply("", "When do you fly?") == "When do you fly?"
        assert claude._join_reply("Noted.", "") == "Noted."


# ── the tool's answer is what was really saved (CodeRabbit #261) ─────────────

def _day(n):
    return (date.today() + timedelta(days=n)).isoformat()


class TestTheToolsAnswer:
    def test_everything_kept_is_simply_saved(self):
        assert claude._tool_answer(ROUTE, dict(ROUTE)) == claude._TOOL_SAVED

    def test_a_value_the_check_only_tidied_is_kept(self):
        """'jfk' is kept as 'JFK'; an empty field was never sent."""
        sent = {"origin": "jfk", "destination": "LHR ", "cabin_class": None}
        assert claude._tool_answer(sent, {"origin": "JFK", "destination": "LHR"}) == claude._TOOL_SAVED

    def test_a_dropped_field_is_named_and_not_confirmed(self):
        sent = {**ROUTE, "departure_date": _day(60), "return_date": _day(50)}
        kept = {**ROUTE, "departure_date": _day(60)}
        answer = claude._tool_answer(sent, kept)
        assert answer.startswith(f"Saved as: origin JFK, destination LHR, departure_date {_day(60)}.")
        assert "Not saved, not valid: return_date." in answer
        assert "Confirm only the details as saved above; ask the client again for what was not saved." in answer
        assert "end your turn" not in answer  # the reply must not stop on a wrong confirmation

    def test_a_value_a_reprompt_changed_is_given_as_saved(self):
        sent = {**ROUTE, "departure_date": _day(60), "return_date": _day(50)}
        kept = {**ROUTE, "departure_date": _day(60), "return_date": _day(70)}
        answer = claude._tool_answer(sent, kept)
        assert f"return_date {_day(70)}" in answer and "Not saved" not in answer

    def test_nothing_kept(self):
        answer = claude._tool_answer({"origin": "London"}, {})
        assert answer.startswith("Saved as: nothing. Not saved, not valid: origin.")


class TestOnlyWhatWasSavedIsConfirmed:
    def test_streamed_a_return_before_the_departure_is_not_answered_saved(self):
        sent = {**ROUTE, "departure_date": _day(60), "return_date": _day(50)}
        client = _client(streams=[
            _Stream([PREAMBLE], _message(_text(PREAMBLE), _tool(sent), stop="tool_use")),
            _Stream([REST], _message(_text(REST))),
        ])
        _text_out, _cost, entities = _stream_turn(client, [])
        assert entities == {**ROUTE, "departure_date": _day(60)}
        answer = client.messages.stream.call_args_list[1].kwargs["messages"][2]["content"][0]["content"]
        assert answer == claude._tool_answer(sent, entities)
        assert "Not saved, not valid: return_date." in answer

    def test_called_the_check_runs_first_and_the_corrected_dates_are_answered(self):
        sent = {**ROUTE, "departure_date": _day(60), "return_date": _day(50)}
        fixed = {**ROUTE, "departure_date": _day(60), "return_date": _day(70)}
        client = _client(creates=[
            _message(_text(PREAMBLE), _tool(sent), stop="tool_use"),
            _message(_tool(fixed, "toolu_fix"), stop="tool_use"),  # the re-prompt
            _message(_text(REST)),
        ])
        with patch.object(claude, "_get_client", return_value=client):
            text, _cost, entities = claude._call_model_with_tools(
                MODEL, SYSTEM, "jfk to london please", max_tokens=450, temperature=0.6,
            )
        assert entities == fixed and text == f"{PREAMBLE} {REST}"
        calls = client.messages.create.call_args_list
        assert len(calls) == 3
        assert "tool_choice" not in calls[1].kwargs          # the re-prompt, before the rest
        rest_call = calls[2].kwargs
        assert rest_call["tool_choice"] == {"type": "none"}
        # The rest answers the call the client's turn made, with the corrected values.
        result = rest_call["messages"][2]["content"][0]
        assert result["tool_use_id"] == "toolu_01"
        assert f"return_date {_day(70)}" in result["content"] and "Not saved" not in result["content"]


class TestEveryCallIsKept:
    def test_two_calls_in_one_reply_are_merged_not_replaced(self):
        response = _message(_text(PREAMBLE), _tool(ROUTE, "toolu_a"),
                            _tool({"cabin_class": "business"}, "toolu_b"), stop="tool_use")
        assert claude._tool_input(response) == {**ROUTE, "cabin_class": "business"}
        assert claude._parse_tool_response(response) == (PREAMBLE, {**ROUTE, "cabin_class": "business"})

    def test_a_later_call_wins_on_the_same_field(self):
        response = _message(_tool({"origin": "JFK"}, "toolu_a"), _tool({"origin": "EWR"}, "toolu_b"), stop="tool_use")
        assert claude._tool_input(response) == {"origin": "EWR"}

    def test_streamed_both_calls_reach_the_lead_and_both_are_answered(self):
        client = _client(streams=[
            _Stream([PREAMBLE], _message(_text(PREAMBLE), _tool(ROUTE, "toolu_a"),
                                         _tool({"cabin_class": "business"}, "toolu_b"), stop="tool_use")),
            _Stream([REST], _message(_text(REST))),
        ])
        _text_out, _cost, entities = _stream_turn(client, [])
        assert entities == {**ROUTE, "cabin_class": "business"}
        results = client.messages.stream.call_args_list[1].kwargs["messages"][2]["content"]
        assert [(r["tool_use_id"], r["content"]) for r in results] == [
            ("toolu_a", claude._TOOL_SAVED), ("toolu_b", claude._TOOL_SAVED),
        ]


# ── non-streamed path ─────────────────────────────────────────────────────────

class TestCalledTurn:
    def test_the_line_before_the_tool_is_followed_by_the_reply(self):
        client = _client(creates=[
            _message(_text(PREAMBLE), _tool(ROUTE), stop="tool_use"),
            _message(_text(REST)),
        ])
        with patch.object(claude, "_get_client", return_value=client):
            text, _cost, entities = claude._call_model_with_tools(
                MODEL, SYSTEM, "jfk to london please", max_tokens=450, temperature=0.6,
            )
        assert text == f"{PREAMBLE} {REST}"
        assert entities == ROUTE
        second = client.messages.create.call_args_list[1].kwargs
        assert second["tool_choice"] == {"type": "none"}
        assert second["messages"][2]["content"][0]["tool_use_id"] == "toolu_01"

    def test_after_a_timeout_the_retry_goes_on_too(self):
        client = _client(creates=[
            anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.invalid")),
            _message(_tool(ROUTE), stop="tool_use"),
            _message(_text(REST)),
        ])
        with patch.object(claude, "_get_client", return_value=client):
            text, _cost, entities = claude._call_model_with_tools(
                MODEL, SYSTEM, "jfk to london please", max_tokens=450, temperature=0.6,
            )
        assert (text, entities) == (REST, ROUTE)
        assert client.messages.create.call_count == 3

    def test_a_rest_that_fails_keeps_the_reply(self):
        client = _client(creates=[
            _message(_text(PREAMBLE), _tool(ROUTE), stop="tool_use"),
            RuntimeError("socket closed"),
        ])
        with patch.object(claude, "_get_client", return_value=client):
            text, _cost, entities = claude._call_model_with_tools(
                MODEL, SYSTEM, "jfk to london please", max_tokens=450, temperature=0.6,
            )
        assert (text, entities) == (PREAMBLE, ROUTE)


# ── through the real SDK: what reaches the API ──────────────────────────────

def _sse(*events) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def _start(msg_id):
    return {"type": "message_start", "message": {
        "id": msg_id, "type": "message", "role": "assistant", "model": MODEL, "content": [],
        "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 1}}}


def _text_block(index, words):
    return [
        {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": words}},
        {"type": "content_block_stop", "index": index},
    ]


def _end(stop):
    return [
        {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None}, "usage": {"output_tokens": 30}},
        {"type": "message_stop"},
    ]


class TestThroughTheSdk:
    def test_the_api_gets_plain_blocks_and_the_tools_answer(self):
        first = _sse(
            _start("msg_1"),
            *_text_block(0, PREAMBLE),
            {"type": "content_block_start", "index": 1, "content_block": {
                "type": "tool_use", "id": "toolu_01", "name": "save_travel_details", "input": {}}},
            {"type": "content_block_delta", "index": 1, "delta": {
                "type": "input_json_delta", "partial_json": json.dumps(ROUTE)}},
            {"type": "content_block_stop", "index": 1},
            *_end("tool_use"),
        )
        second = _sse(_start("msg_2"), *_text_block(0, REST), *_end("end_turn"))
        bodies: list[dict] = []

        def answer(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            sse = first if len(bodies) == 1 else second
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse)

        sdk = anthropic.Anthropic(api_key="test-key", max_retries=0,
                                  http_client=httpx.Client(transport=httpx.MockTransport(answer)))
        sent: list[str] = []
        with patch.object(claude, "_get_client", return_value=sdk):
            text, _cost, entities = claude._stream_model_with_tools(
                MODEL, SYSTEM, "jfk to london please", max_tokens=450, temperature=0.6,
                on_chunk=sent.append,
            )

        assert text == f"{PREAMBLE} {REST}" and entities == ROUTE
        assert "".join(sent) == f"{PREAMBLE} {REST}"
        assert len(bodies) == 2
        rest_call = bodies[1]
        assert rest_call["stream"] is True
        assert rest_call["tool_choice"] == {"type": "none"}
        assert [t["name"] for t in rest_call["tools"]] == ["save_travel_details"]
        assert rest_call["system"] == bodies[0]["system"]
        # Exactly the fields the API takes: nothing the SDK's own objects carry
        # (caller, toolset_name, citations) goes back.
        assert rest_call["messages"][1:] == [
            {"role": "assistant", "content": [
                {"type": "text", "text": PREAMBLE},
                {"type": "tool_use", "id": "toolu_01", "name": "save_travel_details", "input": ROUTE},
            ]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_01", "content": claude._TOOL_SAVED},
            ]},
        ]
