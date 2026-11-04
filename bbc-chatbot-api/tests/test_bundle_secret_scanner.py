"""The CI secret scanner: catches a leaked VITE_* / key, allows the CI fake."""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

SCANNER = Path(__file__).resolve().parents[2] / "scripts" / "scan_bundle_secrets.py"


def _run(root, env=None):
    full = {k: v for k, v in os.environ.items() if not k.endswith(("KEY", "SECRET", "TOKEN", "PASS", "PASSWORD"))}
    full.update(env or {})
    return subprocess.run([sys.executable, str(SCANNER), str(root)], capture_output=True, text=True, env=full)


def _app(tmp_path, src_line, bundle):
    """A production-shaped app: src/ with the source, dist/ with the BUILT js
    (Vite has already replaced import.meta.env.X with its value)."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.ts").write_text(src_line)
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index-abc123.js").write_text(bundle)
    return tmp_path / "dist"


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


def test_an_inlined_ordinary_token_is_caught_by_its_value(tmp_path):
    # The build replaced the name with the value; only the value is in dist/.
    # built at run time, so the test file itself carries no token-shaped literal
    token = "tok_" + "".join(f"{n:x}" for n in range(9, 1, -1)) * 2
    dist = _app(tmp_path, "export const u = 1;", f'const a="{token}";fetch("/x",{{headers:{{a}}}})')
    r = _run(dist, {"VITE_PARTNER_TOKEN": token})
    assert r.returncode == 1 and "VITE_PARTNER_TOKEN" in r.stderr
    assert token not in r.stderr + r.stdout, "the scanner must never print the secret"


def test_a_secret_named_vite_variable_in_source_fails_before_any_value(tmp_path):
    dist = _app(tmp_path, "const k = import.meta.env.VITE_STRIPE_SECRET;", 'const k="";')
    r = _run(dist)
    assert r.returncode == 1 and "VITE_STRIPE_SECRET" in r.stderr


def test_the_public_decoy_key_and_plain_vars_are_allowed(tmp_path):
    decoy = "DECOY" + "-" + "x" * 16
    dist = _app(
        tmp_path,
        "const d = import.meta.env.VITE_DECOY_BUNDLE_KEY; const u = import.meta.env.VITE_API_URL;",
        f'const d="{decoy}";const u="https://api.example.com";',
    )
    r = _run(dist, {"VITE_DECOY_BUNDLE_KEY": decoy, "VITE_API_URL": "https://api.example.com"})
    assert r.returncode == 0, r.stderr


def test_the_ci_fake_supabase_key_in_the_env_is_not_a_finding(tmp_path):
    body = base64.urlsafe_b64encode(json.dumps({"role": "ci-fake"}).encode()).decode().rstrip("=")
    tok = f"eyJhbGciOiJIUzI1NiJ9.{body}.sig"
    dist = _app(tmp_path, "export {};", f'const k="{tok}"')
    assert _run(dist, {"SUPABASE_KEY": tok}).returncode == 0
