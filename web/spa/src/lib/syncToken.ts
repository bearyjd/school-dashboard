import { ApiError } from '../api'

/**
 * The sync token is a server secret the server never sends to the browser. The user types it once per
 * device and it lives only in this browser's localStorage (this origin); clearing site data removes it.
 * When storage is unavailable (private mode) it is kept in memory until the tab closes.
 * Everything that calls POST /api/sync reads it through here. Never log it or put it in a URL or message.
 */
export const SYNC_TOKEN_KEY = 'school-sync-token'

export type SyncErrorCode =
  | 'no_token' | 'wrong_token' | 'rate_limited' | 'not_configured' | 'running' | 'failed' | 'status_failed'

export interface SyncError {
  code: SyncErrorCode
  message: string
}

export const NO_TOKEN: SyncError = { code: 'no_token', message: 'Enter the sync token in Settings' }
export const STATUS_FAILED: SyncError = { code: 'status_failed', message: 'Could not check sync status' }
const DEFAULT_WAIT_S = 60

/** Errors whose fix is to (re-)enter the token in Settings. */
export const needsToken = (e: SyncError | null): boolean => e?.code === 'no_token' || e?.code === 'wrong_token'

let memoryToken = ''

export function getSyncToken(): string {
  try {
    const stored = (localStorage.getItem(SYNC_TOKEN_KEY) ?? '').trim()
    if (stored) return stored
  } catch {
    // storage unavailable: fall through to the in-memory copy
  }
  return memoryToken
}

export type SaveResult = 'saved' | 'tab' | 'empty'

/** 'saved' = in localStorage; 'tab' = storage unavailable, kept in memory until the tab closes. */
export function setSyncToken(value: string): SaveResult {
  const token = value.trim()
  if (!token) return 'empty'
  memoryToken = token
  try {
    localStorage.setItem(SYNC_TOKEN_KEY, token)
    return 'saved'
  } catch {
    return 'tab'
  }
}

export function clearSyncToken(): void {
  memoryToken = ''
  try {
    localStorage.removeItem(SYNC_TOKEN_KEY)
  } catch {
    // storage unavailable: nothing was stored
  }
}

/** A short, user-facing error for a failed sync call; never the raw error. */
export function syncErrorMessage(e: unknown): SyncError {
  if (!(e instanceof ApiError)) return { code: 'failed', message: 'Sync failed' }
  switch (e.status) {
    case 401: return { code: 'wrong_token', message: 'Wrong token' }
    case 429: return { code: 'rate_limited', message: `Too many attempts, wait ${e.retryAfter ?? DEFAULT_WAIT_S} s` }
    case 501: return { code: 'not_configured', message: 'Syncing is not configured' }
    case 409: return { code: 'running', message: 'A sync is already running' }
    default: return { code: 'failed', message: 'Sync failed' }
  }
}
