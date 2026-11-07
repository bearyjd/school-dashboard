import { render } from '@testing-library/react'
import { expect, test } from 'vitest'
import { FreshnessTag } from './FreshnessTag'

test('stale shows as-of age text', () => {
  const { container } = render(<FreshnessTag ageHours={30} stale />)
  expect(container.textContent).toMatch(/as of 1d 6h ago/i)
})

test('failing shows the failing wording', () => {
  const { container } = render(<FreshnessTag ageHours={5} failing />)
  expect(container.textContent).toMatch(/failing/i)
  expect(container.textContent).toMatch(/5h/)
})

test('failing with no known age', () => {
  const { container } = render(<FreshnessTag ageHours={null} failing />)
  expect(container.textContent).toMatch(/failing/i)
})

test('fresh data shows its age without a warning', () => {
  const { container } = render(<FreshnessTag ageHours={0.2} />)
  expect(container.textContent).toMatch(/just now/i)
  expect(container.textContent).not.toMatch(/stale|failing/i)
})
