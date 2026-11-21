import { expect, test } from 'vitest'
import { portalLink, rowActions } from './actions'
import { link, ritem } from './fixtures'

const asn = (over: Parameters<typeof ritem>[0]) => ritem(over)

test('stage 1 assignment: Done and Turned in, then More', () => {
  expect(rowActions(asn({ id: 1 }), 'parent')).toEqual(['done', 'turned_in', 'more'])
})

test('stage 2: Turned in and Undo', () => {
  expect(rowActions(asn({ id: 1, stage: 2, flag: 'waiting_on_teacher', bucket: 'waiting_on_teacher' }), 'parent'))
    .toEqual(['turned_in', 'undo', 'more'])
})

test('stage 3: Undo only', () => {
  expect(rowActions(asn({ id: 1, stage: 3, flag: 'waiting_on_teacher', bucket: 'waiting_on_teacher' }), 'parent'))
    .toEqual(['undo', 'more'])
})

test('needs-action rows add Draft note for a parent, first', () => {
  const rows = [
    asn({ id: 1, flag: 'missing', bucket: 'needs_action' }),
    asn({ id: 2, stage: 3, flag: 'disputed', bucket: 'needs_action' }),
    asn({ id: 3, stage: 2, flag: 'done_not_received', bucket: 'needs_action' }),
  ]
  for (const r of rows) expect(rowActions(r, 'parent')[0]).toBe('draft_note')
})

test('kid mode never offers Draft note, and More is history only (no teacher confirmation)', () => {
  const r = asn({ id: 1, stage: 2, flag: 'done_not_received', bucket: 'needs_action' })
  expect(rowActions(r, 'kid')).toEqual(['turned_in', 'undo', 'more'])
  expect(rowActions(r, 'kid')).not.toContain('draft_note')
})

test('tasks: Done only (open) and Undo only (closed)', () => {
  expect(rowActions(asn({ id: 1, kind: 'task', links: [] }), 'parent')).toEqual(['done'])
  expect(rowActions(asn({ id: 1, kind: 'task', stage: 2, flag: null, bucket: null }), 'parent')).toEqual(['undo'])
})

test('practice rows have no actions', () => {
  expect(rowActions(asn({ id: 1, kind: 'practice' }), 'parent')).toEqual([])
})

test('portalLink: first live link with a safe http(s) url, else null', () => {
  expect(portalLink(asn({ id: 1 }))).toBe('https://portal.example.org/a/1')
  expect(portalLink(asn({ id: 1, links: [link({ url: 'javascript:alert(1)' })] }))).toBeNull()
  expect(portalLink(asn({ id: 1, links: [link({ url: null })] }))).toBeNull()
  expect(portalLink(asn({ id: 1, links: [link({ live: false })] }))).toBeNull()
  expect(portalLink(asn({ id: 1, links: [link({ url: 'data:text/html,x', id: 1 }), link({ id: 2, url: 'https://ok.example/x' })] })))
    .toBe('https://ok.example/x')
})
