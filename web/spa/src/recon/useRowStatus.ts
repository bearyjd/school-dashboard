import { useCallback, useState } from 'react'

export interface RowStatus {
  pending: boolean
  error: string | null
}

export const IDLE: RowStatus = { pending: false, error: null }

/** Per-row "saving" flag and last error message, kept immutably by assignment id. */
export function useRowStatus() {
  const [map, setMap] = useState<Readonly<Record<number, RowStatus>>>({})
  const set = useCallback((id: number, status: RowStatus) => setMap(prev => ({ ...prev, [id]: status })), [])
  const statusOf = useCallback((id: number): RowStatus => map[id] ?? IDLE, [map])
  return { statusOf, set }
}
