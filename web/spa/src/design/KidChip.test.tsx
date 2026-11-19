import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import { KidChip } from './KidChip'

test('shows the first initial only by default', () => {
  render(<KidChip name="Zed" color="green" />)
  expect(screen.getByText('Z')).toBeTruthy()
  expect(screen.queryByText('Zed')).toBeNull()
})

test('wide adds the name', () => {
  render(<KidChip name="Zed" color="amber" wide />)
  expect(screen.getByText('Zed')).toBeTruthy()
})

test('named colour becomes its token class', () => {
  const { container } = render(<KidChip name="Q" color="lavender" />)
  expect(container.firstElementChild?.classList.contains('kid-lavender')).toBe(true)
})

test('unknown colour falls back to a palette colour', () => {
  const { container } = render(<KidChip name="Q" color="chartreuse" />)
  expect(container.firstElementChild?.classList.contains('kid-blue')).toBe(true)
})

test('compact chip is labelled with the full name for assistive tech', () => {
  render(<KidChip name="Zed" color="green" />)
  expect(screen.getByLabelText('Zed')).toBeTruthy()
})

test('empty name does not crash', () => {
  const { container } = render(<KidChip name="" color="green" />)
  expect(container.firstElementChild).toBeTruthy()
})
