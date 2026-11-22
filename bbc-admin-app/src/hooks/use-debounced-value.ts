import { useEffect, useState } from 'react'

/**
 * `value` once it has stopped changing for `ms`: the value a search request goes out with, while the input
 * stays immediate. `useDeferredValue` is not this: it hands every keystroke on, one request per prefix
 * (CodeRabbit #273: the message search's limiter answered 429 to the finished word).
 */
export function useDebouncedValue<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms)
    return () => clearTimeout(timer)
  }, [value, ms])
  return settled
}
