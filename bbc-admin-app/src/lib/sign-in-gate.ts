/**
 * The CRM's sign-in that runs at boot (crm-embed-auth.ts), while it runs.
 *
 * The CRM opens the panel with its sign-in token, and the cookie may still
 * hold a session: on a browser shared between shifts, the previous agent's.
 * The sign-in guard (routes/_authenticated/route.tsx) waits here until that
 * sign-in has settled, so nothing loads or renders from the old session in
 * the meantime: no chat list, no heartbeat, no request with its token.
 *
 * Pure on purpose — node-tested directly (path aliases don't resolve there).
 */

let pending: Promise<unknown> | null = null

/** Hold the sign-in guard until `signIn` settles, whether it worked or not. */
export function holdSignInUntil(signIn: Promise<unknown>): void {
  pending = signIn
  const release = () => {
    if (pending === signIn) pending = null
  }
  signIn.then(release, release)
}

/**
 * Runs `check` now, or once the CRM's sign-in has settled if one is running.
 * What `check` throws (the guard's redirect to sign-in) reaches the caller
 * either way: thrown now, or as the returned promise's rejection.
 */
export function afterCrmSignIn<T>(check: () => T): T | Promise<T> {
  const signIn = pending
  if (!signIn) return check()
  return signIn.then(check, check)
}
