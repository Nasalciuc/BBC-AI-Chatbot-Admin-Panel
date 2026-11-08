# The schema in the repository = the schema in production (PR-14)

## What is in place

- `bbc-chatbot-api/ci/replay_migrations.sh` replays every migration, in
  order, on an empty Postgres 16 (`ci/bootstrap_supabase.sql` first creates
  the Supabase roles and schemas the migrations touch). Each file runs on its
  own — the `CONCURRENTLY` index files cannot run inside a transaction.
- `bbc-chatbot-api/ci/check_code_columns.py` then checks that every column
  the code names in a literal `.select("…")` exists.
- Both run in CI as the `db-replay` job.
- `bbc-chatbot-api/ci/schema_dictionary.py` writes `DICTIONARY.md` (this
  folder) from a database — today from the replay.

Repairs that made the replay pass (it stopped at 011 before): the role
CHECK lookup in 011/013, the non-IMMUTABLE index predicate in 022, the
REVOKE/ALTER on functions created outside the repo in 031. 044 adds what
production has and no migration wrote (`pipeline_runs.tunnel`,
`had_fallback`, `users.chats_served_today`, `chats_served_date`, the
`needs_agent` status); every statement there is a no-op on production.

## What waits for the owner

1. **The baseline from the live project.** Only the owner can take it:

       supabase db dump --schema public -f /tmp/baseline_public.sql

   Then restore it into a scratch database and run, from the repository
   root, with `PG*` pointing at it:

       python bbc-chatbot-api/ci/check_code_columns.py
       python bbc-chatbot-api/ci/schema_dictionary.py --source "live dump of YYYY-MM-DD" \
           --out docs/schema/DICTIONARY_LIVE.md

   and diff `DICTIONARY_LIVE.md` against `DICTIONARY.md`. Every difference is
   either a migration to add (live has it, the repo does not) or something
   production lost. The dump goes into the repository as
   `migrations/000_baseline.sql` only once the repository is private.

2. **Running the new files in the SQL editor**, in this order, each one
   alone (none is applied):
   - `044_reconcile_schema.sql` (expected to change nothing live)
   - `045`–`047` index files, one statement at a time (`CONCURRENTLY`).
     A concurrent build that fails or is cancelled leaves an `INVALID` index
     that `IF NOT EXISTS` then skips silently, so after each file:

         SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid
          WHERE NOT i.indisvalid;

     must return nothing. If it names one: `DROP INDEX CONCURRENTLY <name>;`
     and run the statement again.
   - `048_drop_redundant_indexes.sql` — after checking the four definitions
     live (`\d+ messages`, `\d+ leads`, `\d+ conversations`)
   - `049_rls_everywhere.sql` — the API uses service_role, which bypasses
     RLS; check nothing else uses the anon key against these tables first
   - `050_drop_leads_flexible_dates.sql` — **only after** this PR's code is
     deployed (older builds still read the column)
   - `051_tasks_follow_ups.sql` is the redesign's (the Tasks page, #242), not
     part of this list: it depends on none of 036–050, and the replay passes
     with it before them or after them. Run it when #242 merges.

3. **Retention** (⚑12) — `POST /api/cron/retention` counts what it would
   delete until `RETENTION_ENABLED=true` is set; schedule it nightly.
