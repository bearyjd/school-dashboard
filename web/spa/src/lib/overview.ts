import { keepPreviousData, useQuery, type UseQueryResult } from '@tanstack/react-query'
import { fetchOverview } from '../api'
import type { Overview } from '../api/types'

/** Prefix of every overview query; invalidating or updating by it covers the `past` variant too. */
export const OVERVIEW_KEY = ['overview'] as const

/**
 * Normalised homework/events/freshness payload. Previous data is kept (with `isError` set) when a refetch fails.
 * `past` also asks for events of past-season teams (Events page only).
 */
export function useOverview(past = false): UseQueryResult<Overview> {
  return useQuery({
    queryKey: past ? [...OVERVIEW_KEY, 'past'] : OVERVIEW_KEY,
    queryFn: () => fetchOverview(past),
    staleTime: 60_000,
    placeholderData: keepPreviousData, // toggling `past` keeps the page (and its checkbox) up while it loads
  })
}
