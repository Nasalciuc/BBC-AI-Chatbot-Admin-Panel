import { useEffect, useState } from 'preact/hooks'

// The chat over the whole screen, with bigger text, on computers (owner's request, 16 Nov): many of the
// clients are over 70 and read the 400px corner panel with difficulty. One switch for every panel (the
// form and the chat), set from the button in the panel's top and remembered on this browser, so a client
// who chose it once finds the chat big again. Phones are full-screen already: the CSS applies it from
// 768px up only, and the button is not drawn below that.

export const LARGE_KEY = 'bbc_widget_large'

const listeners = new Set<(on: boolean) => void>()
let large = readLarge()

/** The stored choice; storage blocked or unset → the usual panel. */
export function readLarge(): boolean {
  try { return localStorage.getItem(LARGE_KEY) === '1' } catch { return false }
}

export function isLarge(): boolean {
  return large
}

export function setLarge(on: boolean): void {
  large = on
  try {
    if (on) localStorage.setItem(LARGE_KEY, '1')
    else localStorage.removeItem(LARGE_KEY)
  } catch { /* storage blocked: the choice lasts for this page */ }
  listeners.forEach(l => l(on))
}

/** [on, set]: every panel that uses it follows the same switch at once. */
export function useLarge(): [boolean, (on: boolean) => void] {
  const [on, set] = useState(large)
  useEffect(() => {
    listeners.add(set)
    set(large)
    return () => { listeners.delete(set) }
  }, [])
  return [on, setLarge]
}
