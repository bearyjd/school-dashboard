import { expect, test } from 'vitest'
import { CONFIG, ritem, sampleItems, summaryOf } from './fixtures'
import { buildKidViews, ixlLine } from './group'

const views = (items = sampleItems(), kid = '', over = {}) =>
  buildKidViews({ config: CONFIG, summary: summaryOf(items, over), items, kid })

test('kids follow the roster order, all kids shown by default', () => {
  expect(views().map(v => v.name)).toEqual(['Alpha', 'Beta', 'Gamma'])
})

test('a single-kid view shows only that kid; an unknown kid means everyone', () => {
  expect(views(sampleItems(), 'Beta').map(v => v.name)).toEqual(['Beta'])
  expect(views(sampleItems(), 'Nobody')).toHaveLength(3)
})

test('buckets come in order Needs action, Do tonight, Waiting on teacher; empty ones are omitted', () => {
  const alpha = views()[0]
  expect(alpha.buckets.map(b => b.id)).toEqual(['needs_action', 'do_tonight', 'waiting_on_teacher'])
  const beta = views()[1]
  expect(beta.buckets.map(b => b.id)).toEqual(['do_tonight'])
})

test('Needs action is sub-ordered Disputed, Missing, Done not received', () => {
  const na = views()[0].buckets[0]
  expect(na.groups.map(g => g.label)).toEqual(['Disputed', 'Missing', 'Done, not received'])
  expect(na.groups.map(g => g.items.map(i => i.title))).toEqual([['Poster'], ['Reading log'], ['Map quiz']])
  expect(na.count).toBe(3)
})

test('empty sub-groups are omitted', () => {
  const items = [ritem({ id: 1, flag: 'missing', bucket: 'needs_action' })]
  expect(views(items)[0].buckets[0].groups.map(g => g.label)).toEqual(['Missing'])
})

test('items keep the server order (due date) inside a group', () => {
  const items = [ritem({ id: 2, title: 'B', due: '2026-11-21' }), ritem({ id: 1, title: 'A', due: '2026-11-19' })]
  const sorted = [items[1], items[0]]
  expect(views(sorted)[0].buckets[0].groups[0].items.map(i => i.title)).toEqual(['A', 'B'])
})

test('practice and upcoming rows are not rows; tasks are', () => {
  const beta = views()[1]
  const titles = beta.buckets.flatMap(b => b.groups.flatMap(g => g.items.map(i => i.title)))
  expect(titles).toEqual(['Fractions', 'Pack gym bag'])
})

test('counts come from the shown rows', () => {
  expect(views()[0].counts).toMatchObject({ needs_action: 3, do_tonight: 1, waiting_on_teacher: 1 })
})

test('IXL line lists subjects with work remaining', () => {
  const beta = views()[1]
  expect(beta.ixl).toBe('IXL: Math 4 remaining, ELA 2 remaining')
  expect(views()[0].ixl).toBeNull()
  expect(ixlLine([])).toBeNull()
  const done = ritem({ id: 9, kind: 'practice', class: 'Math', links: [{ ...sampleItems()[7].links[0], raw_status: '0 remaining' }], flag: null, bucket: null })
  expect(ixlLine([done])).toBe('IXL: nothing remaining')
})

test('state: items / clear / unverified clear', () => {
  expect(views()[0].state).toBe('items')
  expect(views()[2].state).toBe('clear')
  expect(views(sampleItems(), '', { ingest_ok: false })[2].state).toBe('unverified_clear')
  expect(views(sampleItems(), '', { unverified_sources: ['schoology'] })[2].state).toBe('unverified_clear')
  const flagged = [...sampleItems(), ritem({ id: 20, kid: 'Gamma', flag: 'upcoming', bucket: null, unverified: true, unverified_sources: ['schoology'] })]
  expect(views(flagged)[2].state).toBe('unverified_clear')
})

test('kid colour comes from the config; unknown kids get a neutral colour', () => {
  expect(views()[0].color).toBe('blue')
  const extra = [...sampleItems(), ritem({ id: 30, kid: 'Zed' })]
  const v = buildKidViews({ config: CONFIG, summary: { ...summaryOf(extra), kids: [...summaryOf(extra).kids, { name: 'Zed', counts: summaryOf(extra).kids[0].counts, practice: [], all_clear: false, all_clear_reason: null }] }, items: extra, kid: '' })
  expect(v.at(-1)?.name).toBe('Zed')
  expect(v.at(-1)?.color).toBe('')
})
