import { useInfiniteQuery, useQuery, type UseQueryResult } from '@tanstack/react-query'
import { getDismissedList, getList, getSummary } from './api'
import type { ListData, ReconSummary } from './types'

export const SUMMARY_KEY = ['recon', 'summary'] as const
export const LIST_KEY = ['recon', 'list'] as const
export const DISMISSED_KEY = ['recon', 'dismissed'] as const
export const DETAIL_KEY = (id: number) => ['recon', 'detail', id] as const

const STALE_MS = 60_000

export const useSummary = (): UseQueryResult<ReconSummary> =>
  useQuery({ queryKey: SUMMARY_KEY, queryFn: getSummary, staleTime: STALE_MS, refetchOnWindowFocus: true })

/** Open items of every kid (the first 500; the Reconcile screen never needs more). */
export const useAssignments = (): UseQueryResult<ListData> =>
  useQuery({ queryKey: LIST_KEY, queryFn: getList, staleTime: STALE_MS, refetchOnWindowFocus: true })

export const useDismissedAssignments = (kid: string, expanded: boolean) =>
  useInfiniteQuery({
    queryKey: [...DISMISSED_KEY, kid],
    queryFn: ({ pageParam }) => getDismissedList(kid, pageParam),
    initialPageParam: 0,
    getNextPageParam: (lastPage, pages) => {
      const loaded = pages.reduce((sum, page) => sum + page.items.length, 0)
      return lastPage.items.length > 0 && loaded < lastPage.total ? loaded : undefined
    },
    enabled: expanded,
    staleTime: STALE_MS,
  })
