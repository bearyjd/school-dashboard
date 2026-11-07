import { act, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter, useLocation } from 'react-router-dom'
import { expect, test } from 'vitest'
import { useUrlState } from './urlState'

const parseKid = (v: string | null): string => (v && /^[\w ]{1,20}$/.test(v) ? v : 'all')
const parseWeek = (v: string | null): number => {
  const n = Number(v)
  return v !== null && Number.isInteger(n) && Math.abs(n) < 100 ? n : 0
}

function setup(initial: string) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <MemoryRouter initialEntries={[initial]}>{children}</MemoryRouter>
  )
  return renderHook(
    () => ({ kid: useUrlState('kid', parseKid, 'all'), week: useUrlState('week', parseWeek, 0), loc: useLocation() }),
    { wrapper },
  )
}

test('reads the value from the query string', () => {
  const { result } = setup('/x?kid=Zed&week=2')
  expect(result.current.kid[0]).toBe('Zed')
  expect(result.current.week[0]).toBe(2)
})

test('junk or missing falls back', () => {
  const { result } = setup('/x?kid=%3Cscript%3E&week=abc')
  expect(result.current.kid[0]).toBe('all')
  expect(result.current.week[0]).toBe(0)
})

test('set round-trips and preserves other params', () => {
  const { result } = setup('/x?kid=Zed&week=1')
  act(() => result.current.kid[1]('Amy'))
  expect(result.current.kid[0]).toBe('Amy')
  expect(result.current.week[0]).toBe(1)
  expect(result.current.loc.search).toContain('week=1')
})

test('setting the fallback removes the param', () => {
  const { result } = setup('/x?kid=Zed')
  act(() => result.current.kid[1]('all'))
  expect(result.current.loc.search).toBe('')
})
