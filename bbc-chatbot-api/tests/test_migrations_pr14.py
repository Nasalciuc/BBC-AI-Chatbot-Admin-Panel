"""PR-14 — the conventions the new migration files must keep."""

import os
import re
from pathlib import Path

import pytest

MIG = Path(__file__).resolve().parent.parent / "migrations"


def _read(name):
    return (MIG / name).read_text(encoding="utf-8")


def _new_files():
    return sorted(p.name for p in MIG.glob("0[4-9][0-9]_*.sql") if int(p.name[:3]) >= 44)


@pytest.mark.parametrize("name", _new_files())
def test_every_new_migration_declares_applied(name):
    assert re.search(r"APPLIED:\s*(da|nu)", _read(name)), name


@pytest.mark.parametrize("name", [n for n in _new_files() if "CONCURRENTLY" in _read(n)])
def test_concurrently_files_have_no_transaction(name):
    sql = re.sub(r"--[^\n]*", "", _read(name)).upper()
    assert "BEGIN" not in sql and "COMMIT" not in sql, name


def test_destructive_drop_column_says_why_on_the_line_above():
    lines = _read("050_drop_leads_flexible_dates.sql").splitlines()
    i = next(k for k, line in enumerate(lines) if "DROP COLUMN" in line)
    assert lines[i - 1].startswith("-- destructive: ")


def test_050_carries_an_explicit_false_across_before_the_drop():
    sql = _read("050_drop_leads_flexible_dates.sql")
    upd = sql.index("SET dates_flexibility = 'exact'")
    assert upd < sql.index("DROP COLUMN")
    stmt = sql[upd: sql.index(";", upd)]
    assert "dates_flexibility IS NULL" in stmt and "flexible_dates = false" in stmt
    # `true` was the column default: it says nothing and is not mapped.
    assert "'flexible'" not in stmt


def test_049_closes_future_functions_with_the_global_default():
    sql = re.sub(r"--[^\n]*", "", _read("049_rls_everywhere.sql"))
    assert re.search(r"ALTER DEFAULT PRIVILEGES\s+REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC", sql)
    assert "IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM anon, authenticated" in sql
    assert "IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC" not in sql


def test_022_index_predicate_is_immutable():
    sql = _read("022_blocklist.sql")
    stmt = sql[sql.index("CREATE INDEX IF NOT EXISTS idx_blocklist_active"):]
    stmt = stmt[: stmt.index(";")]
    assert "now()" not in stmt


def test_011_and_013_find_the_role_check_however_postgres_renders_it():
    for name in ("011_ready_supervisor_moderation.sql", "013_add_qa_dev_roles.sql"):
        sql = _read(name)
        assert "ILIKE '%role IN%'" not in sql.replace("the old ILIKE '%role IN%'", "")
        assert "users_role_check" in sql and "=\\s*ANY" in sql
        # A varchar column renders `(role)::text = ANY`: the gap allows the cast.
        assert "\\mrole\\M[^=]{0,30}" in sql and "[^a-z_]*(=" not in sql


def test_no_code_reads_flexible_dates_any_more():
    """Only comments may still name it — 050 drops the column."""
    app = Path(__file__).resolve().parent.parent / "app"
    for p in app.rglob("*.py"):
        for line in p.read_text(encoding="utf-8").splitlines():
            if "flexible_dates" in line:
                assert "#" in line and line.index("#") < line.index("flexible_dates"), (str(p), line)


def test_replay_tooling_is_present():
    ci = Path(__file__).resolve().parent.parent / "ci"
    assert (ci / "bootstrap_supabase.sql").exists()
    script = (ci / "replay_migrations.sh").read_text()
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    assert "--single-transaction" not in code
    assert os.access(ci / "replay_migrations.sh", os.X_OK)


def test_a_leaked_invite_hash_is_not_a_token():
    """Review fix: presenting the stored SHA-256 must not match its own row
    through the raw-token fallback."""
    from app.db.supabase import _invite_keys, hash_invite_token
    from app.services.email import generate_invite_token

    raw = generate_invite_token()
    assert _invite_keys(raw) == [hash_invite_token(raw), raw]   # pre-deploy links still work
    leaked = hash_invite_token(raw)
    assert _invite_keys(leaked) == [hash_invite_token(leaked)]  # never itself
