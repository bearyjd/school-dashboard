import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { ApiError } from '../api'
import { clearSyncToken, getSyncToken, needsToken, NO_TOKEN, setSyncToken, syncErrorMessage, SYNC_TOKEN_KEY } from './syncToken'

beforeEach(() => { clearSyncToken(); localStorage.clear() })
afterEach(() => vi.restoreAllMocks())

test('missing token reads as empty string', () => {
  expect(getSyncToken()).toBe('')
})

test('set trims, get returns it, clear removes it', () => {
  expect(setSyncToken('  abc123  ')).toBe('saved')
  expect(localStorage.getItem(SYNC_TOKEN_KEY)).toBe('abc123')
  expect(getSyncToken()).toBe('abc123')
  clearSyncToken()
  expect(getSyncToken()).toBe('')
  expect(localStorage.getItem(SYNC_TOKEN_KEY)).toBeNull()
})

test('blank input is not saved', () => {
  expect(setSyncToken('   ')).toBe('empty')
  expect(localStorage.getItem(SYNC_TOKEN_KEY)).toBeNull()
})

test('storage errors never throw and fall back to memory for this tab', () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('denied') })
  expect(getSyncToken()).toBe('')
  expect(setSyncToken('abc')).toBe('tab')
  expect(getSyncToken()).toBe('abc')
  expect(() => clearSyncToken()).not.toThrow()
  expect(getSyncToken()).toBe('')
})

test('error messages are short sentences with a code, never raw errors', () => {
  expect(syncErrorMessage(new ApiError('unauthorized', 401))).toEqual({ code: 'wrong_token', message: 'Wrong token' })
  expect(syncErrorMessage(new ApiError('too many attempts', 429, 42)))
    .toEqual({ code: 'rate_limited', message: 'Too many attempts, wait 42 s' })
  expect(syncErrorMessage(new ApiError('too many attempts', 429)).message).toBe('Too many attempts, wait 60 s')
  expect(syncErrorMessage(new ApiError('SYNC_TOKEN not configured', 501)))
    .toEqual({ code: 'not_configured', message: 'Syncing is not configured' })
  expect(syncErrorMessage(new ApiError('sync already running', 409)).code).toBe('running')
  expect(syncErrorMessage(new ApiError('boom TESTTOKEN', 500))).toEqual({ code: 'failed', message: 'Sync failed' })
  expect(syncErrorMessage(new TypeError('Failed to fetch')).code).toBe('failed')
})

test('only missing/wrong token errors send the user to Settings', () => {
  expect(needsToken(NO_TOKEN)).toBe(true)
  expect(needsToken(syncErrorMessage(new ApiError('x', 401)))).toBe(true)
  expect(needsToken(syncErrorMessage(new ApiError('x', 501)))).toBe(false)
  expect(needsToken(null)).toBe(false)
})
