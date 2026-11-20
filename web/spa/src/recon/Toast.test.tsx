import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { Toast } from './Toast'

beforeEach(() => vi.useFakeTimers())
afterEach(() => vi.useRealTimers())

test('shows the text, offers Undo, and dismisses itself after 5 s', () => {
  const onDismiss = vi.fn()
  const onUndo = vi.fn()
  render(<Toast text="Marked Essay draft done" onUndo={onUndo} onDismiss={onDismiss} />)
  expect(screen.getByText('Marked Essay draft done').closest('[aria-live="polite"]')).not.toBeNull()
  act(() => { vi.advanceTimersByTime(4900) })
  expect(onDismiss).not.toHaveBeenCalled()
  act(() => { vi.advanceTimersByTime(200) })
  expect(onDismiss).toHaveBeenCalledTimes(1)
})

test('Undo calls back and dismisses', () => {
  const onDismiss = vi.fn()
  const onUndo = vi.fn()
  render(<Toast text="Marked" onUndo={onUndo} onDismiss={onDismiss} />)
  fireEvent.click(screen.getByRole('button', { name: /^undo$/i }))
  expect(onUndo).toHaveBeenCalledTimes(1)
  expect(onDismiss).toHaveBeenCalled()
})

test('without onUndo there is no Undo button', () => {
  render(<Toast text="Saved" onDismiss={() => undefined} />)
  expect(screen.queryByRole('button', { name: /undo/i })).toBeNull()
})
