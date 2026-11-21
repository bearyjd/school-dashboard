import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { getConfig, setWriteTokenHeader } from './api'
import { cacheConfig, parseConfig, readCachedConfig } from './config'
import type { ReconConfig } from './types'

export type ReconMode = 'recon' | 'legacy' | 'loading'
export const CONFIG_KEY = ['recon', 'config'] as const

/**
 * GET /api/v2/config. Any failure (404, network, 5xx, malformed) is `null`, never an error: the app then shows the
 * old Today screen. The last answer is cached in sessionStorage so a reload starts in the right mode; the fresh
 * answer always replaces it (initialDataUpdatedAt 0 makes the cached value stale at once).
 */
export function useReconConfig(): UseQueryResult<ReconConfig | null> {
  return useQuery<ReconConfig | null>({
    queryKey: CONFIG_KEY,
    queryFn: async () => {
      try {
        const config = parseConfig(await getConfig())
        setWriteTokenHeader(config?.write_token_header)
        cacheConfig(config)
        return config
      } catch {
        cacheConfig(null)
        return null
      }
    },
    initialData: () => {
      const cached = readCachedConfig()
      if (cached) setWriteTokenHeader(cached.write_token_header)
      return cached ?? undefined
    },
    initialDataUpdatedAt: 0,
    staleTime: 60_000,
    refetchOnWindowFocus: true,
    retry: false,
  })
}

export function useReconMode(): ReconMode {
  const { data } = useReconConfig()
  if (data === undefined) return 'loading'
  return data !== null && data.enabled && data.migrated ? 'recon' : 'legacy'
}
