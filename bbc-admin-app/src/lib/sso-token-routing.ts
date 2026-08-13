/**
 * Which URL token (if any) the SSO bootstrap may exchange — pure logic,
 * node-testable without a DOM.
 *
 * The global SSO bootstrap used to consume ANY ?token= on load: it POSTed
 * an operator's INVITE token to crm-exchange (401) and stripped the query,
 * so /set-password mounted tokenless → "Invalid Link" for every invited
 * operator. `?token=` on the routes below belongs to the PAGE.
 */

/** Routes whose `?token=` is page-owned (invite / reset links), never SSO. */
export const PUBLIC_TOKEN_ROUTES = ['/set-password']

export function isPublicTokenRoute(pathname: string): boolean {
  return PUBLIC_TOKEN_ROUTES.some(
    (p) => pathname === p || pathname.startsWith(`${p}/`)
  )
}

/**
 * Returns [paramName, value] to exchange, or null to leave the URL alone.
 *
 * `?sso_token=` is the SSO-owned name and wins everywhere. `?token=` stays
 * accepted as the legacy CRM-embed spelling, but ONLY outside the public
 * token routes.
 */
export function pickSsoToken(
  pathname: string,
  search: string
): [string, string] | null {
  const params = new URLSearchParams(search)
  const sso = params.get('sso_token')
  if (sso) return ['sso_token', sso]
  const legacy = params.get('token')
  if (legacy && !isPublicTokenRoute(pathname)) return ['token', legacy]
  return null
}
