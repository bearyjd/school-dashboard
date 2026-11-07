import { expect, test } from 'vitest'
import { event, fresh } from '../test-utils'
import { eventMatchesKid, freshnessKey, problemSources, sourceLabel } from './view'

const unassigned = event({ title: 'Mystery team', kids: [], unassigned: true })
const schoolWide = event({ title: 'No school', kids: [], source: 'school' })

test('unassigned events show only when no kid is selected', () => {
  expect(eventMatchesKid(unassigned, '')).toBe(true)
  expect(eventMatchesKid(unassigned, 'Alpha')).toBe(false)
})

test('school-wide events show under every kid filter', () => {
  expect(eventMatchesKid(schoolWide, '')).toBe(true)
  expect(eventMatchesKid(schoolWide, 'Alpha')).toBe(true)
})

test('kid-specific events match only their kids', () => {
  const e = event({ title: 'Game', kids: ['Alpha', 'Beta'] })
  expect(eventMatchesKid(e, 'Beta')).toBe(true)
  expect(eventMatchesKid(e, 'Gamma')).toBe(false)
})

test('problemSources: stale, failing and never_ok count; paused and healthy do not', () => {
  const f = {
    a: fresh({ stale: true }), b: fresh({ failing: true }), c: fresh({ issue: 'never_ok' }),
    d: fresh({ paused: true, stale: false }), e: fresh(),
  }
  expect(problemSources(f).map(([k]) => k)).toEqual(['a', 'b', 'c'])
})

test('labels: email items are Email, not Manual', () => {
  expect(sourceLabel('email')).toBe('Email')
  expect(freshnessKey('email')).toBeNull()
})
