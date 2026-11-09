// What a token found in the shared cookie means for this tab: another tab
// may have signed in. Pure — pinned by tests/session-swap.test.ts.

/** The claims of a JWT, unverified (the server checks the signature);
 *  null when it isn't one. */
export function jwtClaims(token: string): Record<string, unknown> | null {
  try {
    const part = token.split('.')[1]
    if (!part) return null
    const v: unknown = JSON.parse(atob(part.replace(/-/g, '+').replace(/_/g, '/')))
    return v && typeof v === 'object' ? (v as Record<string, unknown>) : null
  } catch {
    return null
  }
}

/** The signed-in user as a token says. Fields as the auth store keeps them. */
export interface SessionUser {
  accountNo: string
  email: string
  name: string
  role: string
  tunnelScope: string
  exp: number
  avatar_url: string | null
  phone: string
}

/**
 * The user from a token's claims, for the sign-in guard and for a newer token
 * of the same person (a renewal, or a save in Settings → Profile in another
 * tab: the new name, photo and phone come with it).
 */
export function userFromClaims(claims: Record<string, unknown>): SessionUser {
  return {
    accountNo: (claims.sub as string) || '',
    email: (claims.email as string) || '',
    name: (claims.name as string) || '',
    role: (claims.role as string) || 'sales',
    tunnelScope: (claims.tunnel_scope as string) || '',
    exp: typeof claims.exp === 'number' ? claims.exp * 1000 : Date.now() + 86400000,
    avatar_url: (claims.avatar_url as string) || null,
    phone: (claims.phone as string) || '',
  }
}

/**
 *  - 'none':   the cookie holds this tab's token, or none;
 *  - 'token':  the same person in the same role with a newer token (a
 *              renewal), or no user loaded yet: take the token;
 *  - 'reload': someone else, or the same person in another role: this tab's
 *              user, menus and cached data belong to the session before, so
 *              the tab starts again and reads the new one.
 */
export function cookieSessionChange(
  user: { accountNo: string; role: string } | null,
  storeToken: string,
  cookieToken: string,
): 'none' | 'token' | 'reload' {
  if (!cookieToken || cookieToken === storeToken) return 'none'
  if (!user) return 'token'
  const claims = jwtClaims(cookieToken)
  if (!claims) return 'reload'
  // The same reading as the sign-in guard (routes/_authenticated/route.tsx).
  const sub = typeof claims.sub === 'string' ? claims.sub : ''
  const role = typeof claims.role === 'string' && claims.role ? claims.role : 'sales'
  return sub === user.accountNo && role === user.role ? 'token' : 'reload'
}
