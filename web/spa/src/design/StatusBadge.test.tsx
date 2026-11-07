import { render } from '@testing-library/react'
import { expect, test } from 'vitest'
import { StatusBadge } from './StatusBadge'

const STATUSES = ['overdue', 'missing', 'late', 'tonight', 'upcoming'] as const

test.each(STATUSES)('%s renders a word and an aria-hidden icon', status => {
  const { container } = render(<StatusBadge status={status} />)
  expect(container.textContent?.toLowerCase()).toContain(status)
  const icon = container.querySelector('[aria-hidden="true"]')
  expect(icon).toBeTruthy()
  expect(icon?.textContent?.length).toBeGreaterThan(0)
})

test('icons differ between statuses so meaning is not colour-only', () => {
  const icons = STATUSES.map(s => {
    const { container } = render(<StatusBadge status={s} />)
    return container.querySelector('[aria-hidden="true"]')?.textContent
  })
  expect(new Set(icons).size).toBeGreaterThanOrEqual(4)
})
