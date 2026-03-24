 -- Migration 005: Add password_hash and phone columns to users table.
 -- These columns were added manually via Supabase SQL Editor but were missing
 -- from the migration files, causing schema drift.
 -- Idempotent: safe to run on existing databases.

 ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash text;
 ALTER TABLE users ADD COLUMN IF NOT EXISTS phone varchar(50);
 COMMENT ON COLUMN users.phone IS 'Agent phone number — optional, E.164 format';
 