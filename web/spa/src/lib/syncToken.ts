import { ApiError } from '../api'

/**
 * The sync token is a server secret the server never sends to the browser. The user types it once per
 * device and it lives only in this browser's localStorage (this origin); clearing site data removes it.
 * Everything that calls POST /api/sync reads it through here. Never log it or put it in a URL or message.
 */
export const SYNC_TOKEN_KEY = 'school-sync-token'

export const MSG_NO_TOKEN = 'Enter the sync token in Settings'
export const MSG_WRONG_TOKEN = 'Wrong token'
export const MSG_NOT_CONFIGURED = 'Syncing is not configured'
export const MSG_RUNNING = 'A sync is already running'
export const MSG_FAILED = 'Sync failed'
const DEFAULT_WAIT_S = 60

export function getSyncToken(): string {
  try {
    return (localStorage.getItem(SYNC_TOKEN_KEY) ?? '').trim()
  } catch {
    return ''
  }
}

/** Saves the trimmed token; false when it is empty or storage is unavailable. */
export function setSyncToken(value: string): boolean {
  const token = value.trim()
  if (!token) return false
  try {
    localStorage.setItem(SYNC_TOKEN_KEY, token)
    return true
  } catch {
    return false
  }
}

export function clearSyncToken(): void {
  try {
    localStorage.removeItem(SYNC_TOKEN_KEY)
  } catch {
    // storage unavailable: nothing was stored
  }
}

/** A short, user-facing sentence for a failed sync call; never the raw error. */
export function syncErrorMessage(e: unknown): string {
  if (!(e instanceof ApiError)) return MSG_FAILED
  switch (e.status) {
    case 401: return MSG_WRONG_TOKEN
    case 429: return `Too many attempts, wait ${e.retryAfter ?? DEFAULT_WAIT_S} s`
    case 501: return MSG_NOT_CONFIGURED
    case 409: return MSG_RUNNING
    default: return MSG_FAILED
  }
}
