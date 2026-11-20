import type { ReconConfig, WriteAuth } from './types'

/** Last answer of GET /api/v2/config, so the next page load knows the mode without a flash. Holds no secret. */
export const CONFIG_CACHE_KEY = 'school-recon-config'
const WRITE_AUTH: readonly string[] = ['open', 'token', 'unconfigured']

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null

/** The config when `raw` has the expected shape, else null (any other answer means the legacy view). */
export function parseConfig(raw: unknown): ReconConfig | null {
  if (!isRecord(raw) || typeof raw.enabled !== 'boolean' || typeof raw.migrated !== 'boolean') return null
  const kids = Array.isArray(raw.kids)
    ? raw.kids.flatMap((k: unknown) => (isRecord(k) && typeof k.name === 'string'
      ? [{ name: k.name, color: typeof k.color === 'string' ? k.color : '' }] : []))
    : []
  const auth = typeof raw.write_auth === 'string' && WRITE_AUTH.includes(raw.write_auth) ? raw.write_auth as WriteAuth : 'token'
  const num = (v: unknown, d: number) => (typeof v === 'number' && Number.isFinite(v) ? v : d)
  return {
    enabled: raw.enabled, migrated: raw.migrated, kids, write_auth: auth,
    grace_default: num(raw.grace_default, 2),
    write_token_header: typeof raw.write_token_header === 'string' ? raw.write_token_header : 'X-Recon-Token',
    write_token_min_length: num(raw.write_token_min_length, 32),
  }
}

export function readCachedConfig(): ReconConfig | null {
  try {
    return parseConfig(JSON.parse(sessionStorage.getItem(CONFIG_CACHE_KEY) ?? 'null'))
  } catch {
    return null
  }
}

/** `null` forgets the cached answer. */
export function cacheConfig(config: ReconConfig | null): void {
  try {
    if (config) sessionStorage.setItem(CONFIG_CACHE_KEY, JSON.stringify(config))
    else sessionStorage.removeItem(CONFIG_CACHE_KEY)
  } catch {
    // storage unavailable: the next load simply waits for the answer
  }
}
