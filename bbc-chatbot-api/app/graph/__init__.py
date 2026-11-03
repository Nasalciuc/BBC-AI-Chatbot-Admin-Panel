"""LangGraph turn flow (PR-5).

R12 — no external telemetry: langgraph pulls in langsmith, which would ship a
turn's state (messages, contacts) to LangSmith if tracing were on. Force it off
BEFORE any langgraph import, here at package import, and a test asserts it.
"""

import os

for _var in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING_V2"):
    os.environ[_var] = "false"
os.environ.setdefault("LANGCHAIN_ENDPOINT", "")
