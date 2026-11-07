import { fireEvent, render, screen } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { Tabs } from './Tabs'

const ITEMS = [{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }, { id: 'c', label: 'C' }]

function setup(active = 'a') {
  const onChange = vi.fn()
  render(<Tabs items={ITEMS} active={active} onChange={onChange} idPrefix="t" />)
  return onChange
}

test('tab and panel ids are wired with aria-controls', () => {
  setup('b')
  const tab = screen.getByRole('tab', { name: 'B' })
  expect(tab.id).toBe('t-tab-b')
  expect(tab.getAttribute('aria-controls')).toBe('t-panel-b')
})

test('only the active tab is in the tab order', () => {
  setup('b')
  expect(screen.getByRole('tab', { name: 'B' }).tabIndex).toBe(0)
  expect(screen.getByRole('tab', { name: 'A' }).tabIndex).toBe(-1)
  expect(screen.getByRole('tab', { name: 'C' }).tabIndex).toBe(-1)
})

test.each([
  ['ArrowRight', 'a', 'b'],
  ['ArrowRight', 'c', 'a'],
  ['ArrowLeft', 'a', 'c'],
  ['ArrowLeft', 'b', 'a'],
  ['Home', 'c', 'a'],
  ['End', 'a', 'c'],
])('%s from %s selects %s and moves focus', (key, from, to) => {
  const onChange = setup(from)
  fireEvent.keyDown(screen.getByRole('tab', { name: from.toUpperCase() }), { key })
  expect(onChange).toHaveBeenCalledWith(to)
  expect(document.activeElement?.id).toBe(`t-tab-${to}`)
})

test('other keys do nothing', () => {
  const onChange = setup('a')
  fireEvent.keyDown(screen.getByRole('tab', { name: 'A' }), { key: 'x' })
  expect(onChange).not.toHaveBeenCalled()
})
