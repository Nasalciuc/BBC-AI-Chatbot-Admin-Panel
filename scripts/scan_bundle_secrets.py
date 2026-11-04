#!/usr/bin/env python3
"""Fail if a built frontend bundle carries a secret.

A bundler inlines every VITE_* variable it sees, so a service-role key or an
Anthropic key put in the panel's env ships to every visitor's browser. CI
builds the bundles and runs this over dist/. Three checks:

1. Patterns over the built files (a key format, a service_role JWT, ...).
2. The VALUES of the secret-looking variables in this process's environment
   (VITE_*_KEY, SUPABASE_KEY, ...): Vite replaces `import.meta.env.VITE_X`
   with its value, so the name never reaches dist/ and only the value can be
   found there.
3. A build-time policy over each app's own source (src/, index.html, the
   committed .env files, vite.config): no `VITE_*PASS/SECRET/KEY/TOKEN` name
   at all, because whatever value it gets at build time is public.

Allowlist: the CI fake Supabase JWT (role "ci-fake"), which is published on
purpose in tests.yml, and VITE_DECOY_BUNDLE_KEY, a decoy that is public by
design (PR-3: replaying it trips an alert).
"""
from __future__ import annotations

import base64
import json
import os
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
# Public on purpose: the decoy bundle key exists to be found and replayed.
_ALLOW_NAMES = {"VITE_DECOY_BUNDLE_KEY"}
_SECRET_NAME = re.compile(r"(?:PASS(?:WORD)?|SECRET|KEY|TOKEN)$")
_SENSITIVE_VITE = re.compile(r"\bVITE_[A-Z0-9_]*(?:PASS|SECRET|KEY|TOKEN)[A-Z0-9_]*\b")
_MIN_VALUE = 8
_SRC_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".html", ".vue", ".svelte"}


def _jwt_role(tok: str) -> str:
    try:
        payload = tok.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return str(json.loads(base64.urlsafe_b64decode(payload)).get("role", ""))
    except Exception:
        return ""


def secret_values(env: dict[str, str]) -> dict[str, str]:
    """name -> value for the secret-looking variables worth searching for."""
    out = {}
    for name, value in env.items():
        value = (value or "").strip()
        if name in _ALLOW_NAMES or len(value) < _MIN_VALUE or not _SECRET_NAME.search(name):
            continue
        if _JWT.fullmatch(value) and _jwt_role(value) in _ALLOW_ROLES:
            continue
        out[name] = value
    return out


def _jwt_is_service_role(tok: str) -> bool:
    role = _jwt_role(tok)
    return role == "service_role" and role not in _ALLOW_ROLES


def scan_source(app_dir: Path) -> list[str]:
    """The policy: no secret-named VITE_ variable anywhere in the app's source."""
    findings: list[str] = []
    files: list[Path] = []
    src = app_dir / "src"
    if src.is_dir():
        files += [p for p in src.rglob("*") if p.is_file() and p.suffix in _SRC_SUFFIXES]
    files += [p for p in app_dir.glob("index.html")] + [p for p in app_dir.glob("vite.config.*")]
    files += [p for p in app_dir.glob(".env*") if p.is_file()]
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in _SENSITIVE_VITE.finditer(text):
            if m.group(0) not in _ALLOW_NAMES:
                findings.append(f"{path}: secret-named VITE_ variable (its value would be public): {m.group(0)}")
    return findings


def scan(root: Path, values: dict[str, str] | None = None) -> list[str]:
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
        for name, value in (values or {}).items():
            if value in text:
                findings.append(f"{path}: the value of ${name} is inlined")  # never print the value
    return findings


def main(argv: list[str]) -> int:
    roots = [Path(a) for a in argv[1:]] or [Path("bbc-admin-app/dist"), Path("bbc-widget/dist")]
    findings: list[str] = []
    values = secret_values(dict(os.environ))
    for root in roots:
        if root.exists():
            findings += scan(root, values)
        # dist/ is the build of the app one level up: its source is checked too.
        app_dir = root.parent if root.name == "dist" else None
        if app_dir is not None and app_dir.is_dir():
            findings += scan_source(app_dir)
    if findings:
        print("SECRET(S) IN BUNDLE:", file=sys.stderr)
        for f in findings:
            print("  " + f, file=sys.stderr)
        return 1
    print("bundle secret scan: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
