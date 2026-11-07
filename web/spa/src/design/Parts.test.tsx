import { fireEvent, render, screen } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { Tile } from './Tile'
import { Row } from './Row'
import { Tabs } from './Tabs'

test('Tile is a button when clickable and shows label and value', () => {
  const onClick = vi.fn()
  render(<Tile label="Overdue" value={3} tone="overdue" onClick={onClick} />)
  fireEvent.click(screen.getByRole('button', { name: /overdue/i }))
  expect(onClick).toHaveBeenCalled()
  expect(screen.getByText('3')).toBeTruthy()
})

test('Tile without onClick is not a button', () => {
  render(<Tile label="Missing" value={0} tone="missing" />)
  expect(screen.queryByRole('button')).toBeNull()
})

test('Row renders title as text and only safe hrefs as links', () => {
  const { rerender } = render(<Row title="<img src=x>" meta="Math" href="https://example.com/a" />)
  expect(document.querySelector('img')).toBeNull()
  expect(screen.getByRole('link').getAttribute('href')).toBe('https://example.com/a')
  rerender(<Row title="t" href="javascript:alert(1)" />)
  expect(screen.queryByRole('link')).toBeNull()
})

test('Row shows long titles without a link when href is absent', () => {
  render(<Row title={'x'.repeat(5000)} />)
  expect(screen.getByText(/^x+$/)).toBeTruthy()
})

test('Tabs marks the active tab and reports changes', () => {
  const onChange = vi.fn()
  render(<Tabs items={[{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }]} active="a" onChange={onChange} />)
  expect(screen.getByRole('tab', { name: 'A' }).getAttribute('aria-selected')).toBe('true')
  fireEvent.click(screen.getByRole('tab', { name: 'B' }))
  expect(onChange).toHaveBeenCalledWith('b')
})
