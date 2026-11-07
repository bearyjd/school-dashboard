import { expect, test } from 'vitest'
import { localToday, shiftWeek, todayAnchor, weekDays } from './week'

test('todayAnchor is the rolled local day at noon UTC', () => {
  expect(todayAnchor(new Date('2026-11-08T07:30:00Z'), 'America/New_York').toISOString()).toBe('2026-11-07T12:00:00.000Z')
  expect(todayAnchor(new Date('2026-11-08T09:30:00Z'), 'America/New_York').toISOString()).toBe('2026-11-08T12:00:00.000Z')
})

const TZ = 'America/New_York'

test('7 consecutive dates across the 2027-03-14 DST change', () => {
  const days = weekDays(new Date('2027-03-12T17:00:00Z'), TZ, new Date('2027-03-12T17:00:00Z'))
  expect(days.map(d => d.date)).toEqual([
    '2027-03-12', '2027-03-13', '2027-03-14', '2027-03-15', '2027-03-16', '2027-03-17', '2027-03-18',
  ])
})

test('7 consecutive dates across a month end', () => {
  const days = weekDays(new Date('2026-12-29T17:00:00Z'), TZ)
  expect(days.map(d => d.date)).toEqual([
    '2026-12-29', '2026-12-30', '2026-12-31', '2027-01-01', '2027-01-02', '2027-01-03', '2027-01-04',
  ])
})

test('isToday only for the 04:00-rolled local day', () => {
  // 03:30 local on the 8th is still the 7th.
  const now = new Date('2026-11-08T08:30:00Z')
  expect(localToday(now, TZ)).toBe('2026-11-07')
  const days = weekDays(new Date('2026-11-07T17:00:00Z'), TZ, now)
  expect(days.filter(d => d.isToday).map(d => d.date)).toEqual(['2026-11-07'])
  const later = new Date('2026-11-08T09:30:00Z') // 04:30 local
  expect(localToday(later, TZ)).toBe('2026-11-08')
})

test('shiftWeek moves exactly seven local days, also over DST', () => {
  const start = new Date('2027-03-10T17:00:00Z')
  expect(weekDays(shiftWeek(start, 1), TZ)[0].date).toBe('2027-03-17')
  expect(weekDays(shiftWeek(start, -1), TZ)[0].date).toBe('2027-03-03')
  expect(weekDays(shiftWeek(start, 0), TZ)[0].date).toBe('2027-03-10')
})

test('labels include weekday and day number', () => {
  const [d] = weekDays(new Date('2026-11-07T17:00:00Z'), TZ)
  expect(d.label).toMatch(/Sat/)
  expect(d.label).toMatch(/7/)
})
