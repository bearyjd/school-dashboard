import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import { Banner } from './Banner'

test('renders children as plain text, not HTML', () => {
  render(<Banner tone="warn">{'<b>x</b> stale'}</Banner>)
  expect(screen.getByRole('status').textContent).toContain('<b>x</b> stale')
  expect(document.querySelector('b')).toBeNull()
})

test('error tone is an alert', () => {
  render(<Banner tone="error">Down</Banner>)
  expect(screen.getByRole('alert')).toBeTruthy()
})

test('tone has an aria-hidden icon so it is not colour-only', () => {
  const { container } = render(<Banner tone="warn">x</Banner>)
  expect(container.querySelector('[aria-hidden="true"]')).toBeTruthy()
})
