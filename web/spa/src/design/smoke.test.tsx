import { render } from '@testing-library/react'
import { expect, test } from 'vitest'

test('renders_a_component_in_jsdom', () => {
  const { getByTestId } = render(<div data-testid="x">hi</div>)
  expect(getByTestId('x').textContent).toBe('hi')
})
