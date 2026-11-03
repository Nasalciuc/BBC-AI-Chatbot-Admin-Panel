#!/usr/bin/env python3
"""Fail if a built frontend bundle carries a secret.

A bundler inlines every VITE_* variable it sees, so a service-role key or an
Anthropic key put in the panel's env ships to every visitor's browser. CI
builds the bundles and runs this over dist/.

Allowlist: the CI fake Supabase JWT (role "ci-fake"), which is published on
purpose in tests.yml.
"""
from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

PATTERNS = [
    ("vite secret var", re.compile(r"VITE_[A-Z0-9_]*(?:PASS|SECRET|KEY|TOKEN)")),
    ("anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}")),
    ("literal service_role", re.compile(r"service_role")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]
_JWT = re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")
_ALLOW_ROLES = {"ci-fake"}


def _jwt_is_service_role(tok: str) -> bool:
    try:
        payload = tok.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        role = json.loads(base64.urlsafe_b64decode(payload)).get("role", "")
    except Exception:
        return False
    return role == "service_role" and role not in _ALLOW_ROLES


def scan(root: Path) -> list[str]:
    findings: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in {".js", ".css", ".html", ".map", ".json"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for name, rx in PATTERNS:
            for m in rx.finditer(text):
                findings.append(f"{path}: {name}: {m.group(0)[:40]}")
        for m in _JWT.finditer(text):
            if _jwt_is_service_role(m.group(0)):
                findings.append(f"{path}: service_role JWT: {m.group(0)[:24]}…")
    return findings


def main(argv: list[str]) -> int:
    roots = [Path(a) for a in argv[1:]] or [Path("bbc-admin-app/dist"), Path("bbc-widget/dist")]
    findings: list[str] = []
    for root in roots:
        if root.exists():
            findings += scan(root)
    if findings:
        print("SECRET(S) IN BUNDLE:", file=sys.stderr)
        for f in findings:
            print("  " + f, file=sys.stderr)
        return 1
    print("bundle secret scan: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
