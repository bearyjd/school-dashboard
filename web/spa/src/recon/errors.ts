import { ApiError } from '../api'
import { ERROR_MAINTENANCE, ERROR_OFFLINE } from '../api/errors'

const CONFLICTS: Record<string, string> = {
  invalid_transition: 'That change is not allowed from where this is now - reload and try again',
  no_note_for_flag: 'There is no note to write for this one right now',
  merged_away: 'This was merged into another assignment - reload',
  not_migrated: 'Assignments are not set up yet',
  bad_note: 'The note must be 3-500 characters',
  invalid_status: 'That note was already handled',
}
const OFFLINE = 'Offline - not saved'

/** A short, plain-words message for a failed write; never the raw error, never anything from the request. */
export function messageFor(e: unknown): string {
  if (!(e instanceof ApiError)) return OFFLINE
  switch (e.status) {
    case 401: return 'Token not accepted - check Settings'
    case 403: return 'Parents only'
    case 409: return CONFLICTS[e.message] ?? 'This changed on the server - reload and try again'
    case 429: return `Too many attempts - wait ${e.retryAfter === undefined ? 'a minute' : `${e.retryAfter} s`} and try again`
    case 501: return 'Writes are not set up on the server'
    case 503:
      if (e.message === ERROR_MAINTENANCE) return 'Paused for maintenance'
      if (e.message === ERROR_OFFLINE) return OFFLINE
      return 'Busy, try again'
    default: return 'Could not save - try again'
  }
}
