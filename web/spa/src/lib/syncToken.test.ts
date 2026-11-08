import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { ApiError } from '../api'
import {
  clearSyncToken, getSyncToken, setSyncToken, syncErrorMessage, SYNC_TOKEN_KEY,
  MSG_FAILED, MSG_NOT_CONFIGURED, MSG_RUNNING, MSG_WRONG_TOKEN,
} from './syncToken'

beforeEach(() => localStorage.clear())
afterEach(() => vi.restoreAllMocks())

test('missing token reads as empty string', () => {
  expect(getSyncToken()).toBe('')
})

test('set trims, get returns it, clear removes it', () => {
  expect(setSyncToken('  abc123  ')).toBe(true)
  expect(localStorage.getItem(SYNC_TOKEN_KEY)).toBe('abc123')
  expect(getSyncToken()).toBe('abc123')
  clearSyncToken()
  expect(getSyncToken()).toBe('')
})

test('blank input is not saved', () => {
  expect(setSyncToken('   ')).toBe(false)
  expect(localStorage.getItem(SYNC_TOKEN_KEY)).toBeNull()
})

test('storage errors never throw', () => {
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('denied') })
  expect(getSyncToken()).toBe('')
  expect(setSyncToken('abc')).toBe(false)
  expect(() => clearSyncToken()).not.toThrow()
})

test('error messages are short sentences, never raw errors', () => {
  expect(syncErrorMessage(new ApiError('unauthorized', 401))).toBe(MSG_WRONG_TOKEN)
  expect(syncErrorMessage(new ApiError('too many attempts', 429, 42))).toBe('Too many attempts, wait 42 s')
  expect(syncErrorMessage(new ApiError('too many attempts', 429))).toBe('Too many attempts, wait 60 s')
  expect(syncErrorMessage(new ApiError('SYNC_TOKEN not configured', 501))).toBe(MSG_NOT_CONFIGURED)
  expect(syncErrorMessage(new ApiError('sync already running', 409))).toBe(MSG_RUNNING)
  expect(syncErrorMessage(new ApiError('boom TESTTOKEN', 500))).toBe(MSG_FAILED)
  expect(syncErrorMessage(new TypeError('Failed to fetch'))).toBe(MSG_FAILED)
})
