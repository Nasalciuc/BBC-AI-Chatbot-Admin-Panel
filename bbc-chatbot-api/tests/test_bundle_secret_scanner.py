"""The CI secret scanner: catches a leaked VITE_* / key, allows the CI fake."""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

SCANNER = Path(__file__).resolve().parents[2] / "scripts" / "scan_bundle_secrets.py"


def _run(root):
    return subprocess.run([sys.executable, str(SCANNER), str(root)], capture_output=True, text=True)


def test_clean_bundle_passes(tmp_path):
    (tmp_path / "app.js").write_text("const x = 1; console.log(x)")
    assert _run(tmp_path).returncode == 0


def test_vite_pass_fails(tmp_path):
    (tmp_path / "app.js").write_text('const P="x";const u=import.meta.env.VITE_API_PASS;')
    r = _run(tmp_path)
    assert r.returncode == 1 and "VITE_API_PASS" in r.stderr


def test_anthropic_key_fails(tmp_path):
    (tmp_path / "app.js").write_text('const k="sk-ant-api03-abc123def456"')
    assert _run(tmp_path).returncode == 1


def test_service_role_jwt_fails(tmp_path):
    body = base64.urlsafe_b64encode(json.dumps({"role": "service_role"}).encode()).decode().rstrip("=")
    tok = f"eyJhbGciOiJIUzI1NiJ9.{body}.sig"
    (tmp_path / "app.js").write_text(f'const k="{tok}"')
    assert _run(tmp_path).returncode == 1


def test_ci_fake_jwt_is_allowed(tmp_path):
    body = base64.urlsafe_b64encode(json.dumps({"role": "ci-fake"}).encode()).decode().rstrip("=")
    tok = f"eyJhbGciOiJIUzI1NiJ9.{body}.sig"
    (tmp_path / "app.js").write_text(f'const k="{tok}"')
    assert _run(tmp_path).returncode == 0
