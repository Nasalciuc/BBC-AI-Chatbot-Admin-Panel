import { useEffect, useState } from 'react'

/** The value, once it has stopped changing for `delayMs` (PR-10: a search box
 *  no longer fires one request per keystroke). */
export function useDebouncedValue<T>(value: T, delayMs = 300): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(id)
  }, [value, delayMs])
  return debounced
}
