-- GO-04: new accounts must NOT be auto-assignable by default.
-- is_ready=true at creation made every invite a phantom instant:
-- logged in, heartbeat alive, zero intention to work chats,
-- absorbing assignments that burn through cooldown to permanent death.
-- Applied manually in Supabase BEFORE deploy (repo ≠ schema ground truth).
ALTER TABLE users ALTER COLUMN is_ready SET DEFAULT false;
