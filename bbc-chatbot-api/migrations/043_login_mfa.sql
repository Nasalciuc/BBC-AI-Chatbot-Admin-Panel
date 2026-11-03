-- 043: a second factor for the privileged roles (PR-13).
--
-- users.totp_secret_enc  — the TOTP secret, encrypted with MFA_ENCRYPTION_KEY
--                          (Fernet); never stored or logged in clear.
-- users.mfa_enrolled_at  — set when the first code is confirmed. A secret
--                          without it is an enrolment in progress.
-- mfa_recovery_codes     — 10 per enrolment, bcrypt hashes, each used once.
-- login_locations        — where an account has logged in from (country,
--                          network), for the "new place" alert.
--
-- APPLIED: nu. Until it is, MFA is inert: login works exactly as before and
-- /health/detail.mfa reports unavailable — nobody is locked out.

ALTER TABLE public.users ADD COLUMN IF NOT EXISTS totp_secret_enc text;
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS mfa_enrolled_at timestamptz;

CREATE TABLE IF NOT EXISTS public.mfa_recovery_codes (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    code_hash   text NOT NULL,
    used_at     timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_mfa_recovery_codes_user
    ON public.mfa_recovery_codes(user_id) WHERE used_at IS NULL;

CREATE TABLE IF NOT EXISTS public.login_locations (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id        uuid NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    place          text NOT NULL,          -- country code, or a network prefix
    first_seen_at  timestamptz NOT NULL DEFAULT now(),
    last_seen_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, place)
);

ALTER TABLE public.mfa_recovery_codes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS mfa_recovery_codes_service_role_all ON public.mfa_recovery_codes;
CREATE POLICY mfa_recovery_codes_service_role_all ON public.mfa_recovery_codes
  FOR ALL TO service_role USING (true) WITH CHECK (true);

ALTER TABLE public.login_locations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS login_locations_service_role_all ON public.login_locations;
CREATE POLICY login_locations_service_role_all ON public.login_locations
  FOR ALL TO service_role USING (true) WITH CHECK (true);
