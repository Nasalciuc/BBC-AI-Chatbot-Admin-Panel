/**
 * Guards for route strings the panel is about to present as facts.
 */

/** A route whose two endpoints are identical ("LHR → LHR") is extraction
 *  noise — never present it as a hot lead. */
export function isDegenerateRoute(route: string | null | undefined): boolean {
  if (!route) return false
  const parts = route.split('→').map((p) => p.trim()).filter(Boolean)
  return parts.length === 2 && parts[0] === parts[1]
}
