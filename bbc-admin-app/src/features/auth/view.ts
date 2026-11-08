// What the sign-in screens say — pure, pinned by tests/auth-view.test.ts.
// Every state maps to an answer the server really gives
// (app/api/auth_routes.py): 401 wrong details or no password yet, 403 access
// turned off, 429 too many tries, 400 with the reason an invite link failed.

export type SignInProblem =
  | { kind: 'wrong' }
  | { kind: 'not_set_up' }
  | { kind: 'off' }
  | { kind: 'locked'; wait: 'seconds' | 'hour' | 'day' }
  | { kind: 'unreachable' }

/** The answer of POST /api/auth/login, in the sign-in page's words. The 429
 *  comes from app/security/rate_limiter.py, per network address: a burst
 *  (15 tries, then one every 3 s), 100 an hour, or 300 a day. */
export function signInProblem(status: number | null, detail: string): SignInProblem {
  if (status === null) return { kind: 'unreachable' }
  if (status === 429) return { kind: 'locked', wait: /hourly/i.test(detail) ? 'hour' : /daily/i.test(detail) ? 'day' : 'seconds' }
  if (status === 403) return { kind: 'off' }
  if (status === 401 && /not activated/i.test(detail)) return { kind: 'not_set_up' }
  if (status === 401) return { kind: 'wrong' }
  return { kind: 'unreachable' }
}

/** Seconds the Sign in button waits after a burst of tries; the hourly and
 *  daily limits are too long to count down. */
export const BURST_PAUSE_S = 15

export function problemText(p: Exclude<SignInProblem, { kind: 'off' }>): { head: string; text: string } {
  switch (p.kind) {
    case 'wrong':
      return { head: 'That e-mail and password don’t match.', text: 'Check both and try again.' }
    case 'not_set_up':
      return { head: 'This account has no password yet.', text: 'Open the link in your invite e-mail to choose one, or get a new link.' }
    case 'locked':
      return p.wait === 'seconds'
        ? { head: 'Too many tries.', text: 'Wait a few seconds and try again.' }
        : {
            head: 'Too many tries from this network.',
            text: p.wait === 'hour' ? 'Sign in is paused for up to an hour.' : 'Sign in is paused until tomorrow. Ask your administrator if you need in now.',
          }
    case 'unreachable':
      return { head: 'Couldn’t reach the server.', text: 'Check your connection and try again in a moment.' }
  }
}

export const SESSION_NOTICE = {
  head: 'Your session ended after 24 hours.',
  text: 'Sign in again and you’re back where you were.',
}

export function emailOk(e: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[a-z]{2,}$/i.test(e.trim())
}

export function emailError(e: string): string {
  if (!e.trim()) return 'Type your e-mail.'
  return emailOk(e) ? '' : 'That doesn’t look like an e-mail address.'
}

/** The server's one rule for a new password (pydantic: 8 to 128 characters). */
export function passwordRules(pw: string): { ok: boolean; text: string }[] {
  return [{ ok: pw.length >= 8 && pw.length <= 128, text: pw.length > 128 ? 'At most 128 characters' : 'At least 8 characters' }]
}

export type LinkProblem = 'expired' | 'used' | 'superseded' | 'unknown'

/** "expired: This invite link has expired…" → expired. */
export function linkProblem(detail: string): LinkProblem | null {
  const reason = detail.split(':')[0]?.trim()
  return reason === 'expired' || reason === 'used' || reason === 'superseded' || reason === 'unknown' ? reason : null
}

export const LINK_TEXT: Record<LinkProblem, { head: string; text: string; action: 'new_link' | 'sign_in' }> = {
  expired: {
    head: 'This invite link has expired',
    text: 'Invite links work for 48 hours, so this one has stopped. Type the e-mail your invite came to and we’ll send a fresh link.',
    action: 'new_link',
  },
  used: {
    head: 'This link was already used',
    text: 'Your password is set, so the invite link has done its job. Sign in with your password; if you don’t remember it, ask the owner or an administrator.',
    action: 'sign_in',
  },
  superseded: {
    head: 'A newer invite was sent',
    text: 'Only the newest invite link works. Open the most recent invite e-mail, or get a new link.',
    action: 'new_link',
  },
  unknown: {
    head: 'This link doesn’t work',
    text: 'The link may be incomplete. Type the e-mail your invite came to and we’ll send a fresh link.',
    action: 'new_link',
  },
}

/** The CRM sign-in failed: what the server said, in words
 *  (app/api/auth_routes.py crm_sso_exchange + app/security/crm_token.py).
 *  A 401 has several reasons, so the detail decides. */
export type CrmProblem = 'no_account' | 'expired' | 'invalid' | 'off' | 'no_email' | 'not_set_up' | 'unknown'

export function crmProblemKind(status: number | null, detail: string | null): CrmProblem {
  const d = detail ?? ''
  if (status === 401 && /no matching/i.test(d)) return 'no_account'
  if (status === 401 && /expired/i.test(d)) return 'expired'
  if (status === 401) return 'invalid'
  if (status === 403) return 'off'
  if (status === 400) return 'no_email'
  if (status === 503) return 'not_set_up'
  return 'unknown'
}

export function crmProblem(status: number | null, detail: string | null = null): { kind: CrmProblem; head: string; text: string } {
  const kind = crmProblemKind(status, detail)
  const words: Record<CrmProblem, { head: string; text: string }> = {
    no_account: {
      head: 'Your CRM user isn’t in the chat panel',
      text: 'The chat panel has no account with your CRM e-mail. Ask your administrator to invite that address, or sign in with the e-mail your invite came to.',
    },
    expired: {
      head: 'The sign-in from the CRM expired',
      text: 'It only works for a short time. Open the chat from the CRM again, or sign in with your e-mail and password.',
    },
    invalid: {
      head: 'The CRM’s sign-in couldn’t be checked',
      text: 'Open the chat from the CRM again. If it keeps happening, sign in with your e-mail and password and tell your administrator.',
    },
    off: {
      head: 'Your access is turned off',
      text: 'An owner or admin turned off your account, so the panel won’t open. If that’s a mistake, ask your administrator to turn it back on.',
    },
    no_email: {
      head: 'The CRM didn’t send your e-mail',
      text: 'The chat panel finds you by the e-mail the CRM sends, and this sign-in came without one. Sign in with your e-mail and password for now.',
    },
    not_set_up: {
      head: 'Sign-in from the CRM isn’t switched on',
      text: 'This server isn’t set up to accept it yet. Sign in with your e-mail and password.',
    },
    unknown: {
      head: 'Couldn’t sign you in from the CRM',
      text: 'Something went wrong on the way. Open the chat from the CRM again, or sign in with your e-mail and password.',
    },
  }
  return { kind, ...words[kind] }
}

/**
 * Where to go after signing in: the redirect resolved against this site, kept
 * only when it stays on it (path, query and hash). "//evil.com" and
 * "/\evil.com" resolve to another site and give null, like a bad URL.
 */
export function backAfterSignIn(redirect: string | undefined, origin: string): string | null {
  if (!redirect) return null
  try {
    const u = new URL(redirect, origin)
    return u.origin === origin ? `${u.pathname}${u.search}${u.hash}` : null
  } catch {
    return null
  }
}

/** The e-mail inside a CRM sign-in token, to say who the CRM thinks you are.
 *  Read only for display: the server checks the signature. */
export function crmEmail(token: string | null | undefined): string | null {
  if (!token) return null
  try {
    const part = token.split('.')[1]
    if (!part) return null
    const json = JSON.parse(atob(part.replace(/-/g, '+').replace(/_/g, '/')))
    return typeof json?.email === 'string' && json.email ? json.email : null
  } catch {
    return null
  }
}
