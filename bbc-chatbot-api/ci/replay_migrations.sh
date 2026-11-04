#!/usr/bin/env bash
# ci/replay_migrations.sh (PR-14) — replay every migration on a clean Postgres.
#
# Usage: PGHOST=… PGPORT=… PGUSER=… PGDATABASE=… ci/replay_migrations.sh
#
# Each file runs on its own, WITHOUT --single-transaction: the index files
# use CREATE INDEX CONCURRENTLY, which cannot run inside a transaction.
# ON_ERROR_STOP makes the first failing statement fail the job and name
# the file.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
mig="$here/../migrations"

psql -v ON_ERROR_STOP=1 -q -f "$here/bootstrap_supabase.sql"
for f in "$mig"/[0-9][0-9][0-9]_*.sql; do
  echo "── $(basename "$f")"
  psql -v ON_ERROR_STOP=1 -q -f "$f"
done
echo "replayed $(ls "$mig"/[0-9][0-9][0-9]_*.sql | wc -l) migrations"
