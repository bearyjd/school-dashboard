import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test } from 'vitest'
import { BottomNav } from './BottomNav'

function current(path: string): string[] {
  render(<MemoryRouter initialEntries={[path]}><BottomNav /></MemoryRouter>)
  return screen.getAllByRole('link')
    .filter(l => l.getAttribute('aria-current') === 'page')
    .map(l => l.textContent ?? '')
}

test.each(['/tonight', '/overdue', '/missing', '/events'])('%s marks only Lists as current', path => {
  const marked = current(path)
  expect(marked).toHaveLength(1)
  expect(marked[0]).toContain('Lists')
})

test('Today is current only on the home route', () => {
  expect(current('/')[0]).toContain('Today')
})

test('Chat is current on /chat and Today is not', () => {
  const marked = current('/chat')
  expect(marked).toHaveLength(1)
  expect(marked[0]).toContain('Chat')
})

test('no tab is current on an unrelated route', () => {
  expect(current('/classic/home')).toEqual([])
})
