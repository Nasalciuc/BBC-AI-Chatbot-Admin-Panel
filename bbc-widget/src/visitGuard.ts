/**
 * The visit's guards (proactive.ts: a moment asked, shown or used this visit) in sessionStorage, with this page's
 * memory beneath it. A browser that refuses storage (site data blocked, a private window that cannot write) used to
 * read every guard as free: test 1's off arm was asked again at every tick once its "no" came back (CodeRabbit #291).
 * Now a guard written on this page holds for the rest of it; one the browser does keep is read from the browser.
 *
 * No imports: the tests run it in node (which has no sessionStorage: the memory alone).
 */

const memory = new Map<string, string>()

/** The guard: what this page wrote where the browser would not keep it, else what the browser keeps. */
export function visitGet(k: string): string | null {
  const m = memory.get(k)
  if (m !== undefined) return m
  try { return sessionStorage.getItem(k) } catch { return null }
}

/** Writes the guard: in sessionStorage, or in this page's memory when the browser refuses the write. */
export function visitSet(k: string, v: string): void {
  try {
    sessionStorage.setItem(k, v)
    memory.delete(k)
  } catch {
    memory.set(k, v)
  }
}

/** For the tests: forget what this page holds. */
export function resetVisitGuardForTests(): void {
  memory.clear()
}
