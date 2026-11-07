import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import { KidChip } from './KidChip'

test('shows the first initial only by default', () => {
  render(<KidChip name="Zed" index={0} />)
  expect(screen.getByText('Z')).toBeTruthy()
  expect(screen.queryByText('Zed')).toBeNull()
})

test('wide adds the name', () => {
  render(<KidChip name="Zed" index={1} wide />)
  expect(screen.getByText('Zed')).toBeTruthy()
})

test('colour index wraps around the 3-colour palette', () => {
  const { container } = render(<KidChip name="Q" index={5} />)
  expect(container.firstElementChild?.classList.contains('kid-2')).toBe(true)
})

test('empty name does not crash', () => {
  const { container } = render(<KidChip name="" index={0} />)
  expect(container.firstElementChild).toBeTruthy()
})
