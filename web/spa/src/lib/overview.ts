import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { fetchOverview } from '../api'
import type { Overview } from '../api/types'

export const OVERVIEW_KEY = ['overview'] as const

/** Normalised homework/events/freshness payload. Previous data is kept (with `isError` set) when a refetch fails. */
export function useOverview(): UseQueryResult<Overview> {
  return useQuery({ queryKey: OVERVIEW_KEY, queryFn: fetchOverview, staleTime: 60_000 })
}
