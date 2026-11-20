import { expect, test } from 'vitest'
import { ApiError } from '../api'
import { messageFor } from './errors'

test.each([
  [new ApiError('bad_token', 401), 'Token not accepted - check Settings'],
  [new ApiError('write_token_not_configured', 501), 'Writes are not set up on the server'],
  [new ApiError('forbidden_role', 403), 'Parents only'],
  [new ApiError('maintenance', 503), 'Paused for maintenance'],
  [new ApiError('busy', 503), 'Busy, try again'],
  [new ApiError('offline', 503), 'Offline - not saved'],
  [new ApiError('too_many_attempts', 429, 12), 'Too many attempts - wait 12 s and try again'],
  [new ApiError('too_many_attempts', 429), 'Too many attempts - wait a minute and try again'],
  [new TypeError('Failed to fetch'), 'Offline - not saved'],
  [new ApiError('internal', 500), 'Could not save - try again'],
])('%#', (err, text) => {
  expect(messageFor(err)).toBe(text)
})

test.each([
  ['invalid_transition', /not allowed/i],
  ['no_note_for_flag', /no note/i],
  ['merged_away', /merged/i],
  ['not_migrated', /not set up/i],
  ['something_new', /changed/i],
])('409 %s is explained in plain words', (code, re) => {
  expect(messageFor(new ApiError(code, 409))).toMatch(re)
})
