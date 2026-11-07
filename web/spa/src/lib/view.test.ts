import { expect, test } from 'vitest'
import { event } from '../test-utils'
import { eventMatchesKid } from './view'

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
