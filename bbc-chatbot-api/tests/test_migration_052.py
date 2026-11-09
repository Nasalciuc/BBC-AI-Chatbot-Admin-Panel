"""Migration 052: conversations.status accepts 'needs_agent'.

Static checks on the file; the replay (ci/replay_migrations.sh, the db-replay
job) runs it on a clean Postgres 16.
"""

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestMigration052:
    sql = (ROOT / "migrations" / "052_needs_agent_status.sql").read_text(encoding="utf-8")

    def test_it_is_marked_not_applied(self):
        assert "APPLIED: nu" in self.sql

    def test_it_widens_the_column_and_accepts_needs_agent(self):
        assert "ALTER COLUMN status TYPE varchar(20)" in self.sql
        assert "CHECK (status IN ('active', 'pending', 'closed', 'completed', 'needs_agent'))" in self.sql

    def test_one_open_conversation_per_visitor_keeps_its_name(self):
        # The code matches this name on a 23505 (get_or_create_conversation).
        assert "CREATE UNIQUE INDEX uq_conv_active_visitor" in self.sql
        assert "WHERE status IN ('active', 'needs_agent') AND visitor_id IS NOT NULL" in self.sql
        assert "uq_conv_active_visitor" in (ROOT / "app" / "db" / "supabase.py").read_text(encoding="utf-8")

    def test_all_or_nothing_and_never_queues_writes_for_long(self):
        body = self.sql[self.sql.index("BEGIN;"):]
        assert body.rstrip().endswith("COMMIT;")
        assert "SET LOCAL lock_timeout" in body

    def test_the_status_check_is_found_by_column_not_by_text(self):
        # returning_client_status has a CHECK of its own; a text match on
        # "status" must never be what picks the constraint to drop.
        assert "c.conkey = ARRAY[" in self.sql
        assert "~*" not in self.sql
