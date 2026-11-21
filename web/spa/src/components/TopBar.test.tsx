import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { expect, test } from 'vitest'
import { TopBar } from './TopBar'

function active(path: string): string[] {
  render(<MemoryRouter initialEntries={[path]}><TopBar /></MemoryRouter>)
  return screen.getAllByRole('link').filter(l => l.className.includes('top-bar__link--active')).map(l => l.textContent ?? '')
}

test.each(['/', '/today', '/recon'])('%s highlights only the home tab', path => {
  expect(active(path)).toEqual(['Today'])
})

test('another route highlights its own tab, not home', () => {
  expect(active('/tonight')).toEqual(['Tonight'])
})
