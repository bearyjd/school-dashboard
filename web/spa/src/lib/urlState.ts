import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'

/**
 * A single value kept in the query string so a view can be bookmarked or shared.
 * `parse` must tolerate any string (or null) and return a valid value; junk gives `fallback`.
 * Setting the fallback removes the param to keep URLs short. Other params are preserved.
 */
export function useUrlState<T extends string | number>(
  key: string,
  parse: (raw: string | null) => T,
  fallback: T,
): [T, (value: T) => void] {
  const [params, setParams] = useSearchParams()
  const value = parse(params.get(key))
  const set = useCallback(
    (next: T) => {
      setParams(
        prev => {
          const out = new URLSearchParams(prev)
          if (String(next) === String(fallback)) out.delete(key)
          else out.set(key, String(next))
          return out
        },
        { replace: true },
      )
    },
    [key, fallback, setParams],
  )
  return [value, set]
}
