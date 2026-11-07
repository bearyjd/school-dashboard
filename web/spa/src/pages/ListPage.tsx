import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { updateItem } from '../api'
import type { Overview, OverviewEvent, OverviewItem, OverviewStatus } from '../api/types'
import { Banner } from '../design/Banner'
import { KidChip } from '../design/KidChip'
import { StatusBadge } from '../design/StatusBadge'
import { AsOfTag, KidFilter, OverviewFrame } from '../components/OverviewFrame'
import { OVERVIEW_KEY, useOverview } from '../lib/overview'
import { useUrlState } from '../lib/urlState'
import { eventMatchesKid, formatDay, formatTime, itemFreshness, itemMatchesKid, kidIndex, kidParser, sourceLabel } from '../lib/view'
import './ListPage.css'

export type ListKind = 'tonight' | 'overdue' | 'missing' | 'events'

const TITLES: Record<ListKind, string> = {
  tonight: 'Tonight',
  overdue: 'Overdue',
  missing: 'Missing',
  events: 'Events',
}

const KIND_STATUSES: Record<Exclude<ListKind, 'events'>, OverviewStatus[]> = {
  tonight: ['tonight'],
  overdue: ['overdue'],
  missing: ['missing', 'late'],
}

type SortKey = 'kid' | 'title' | 'course' | 'due' | 'source' | 'type'
type SortDir = 'asc' | 'desc'
const SORT_KEYS: SortKey[] = ['kid', 'title', 'course', 'due', 'source', 'type']

/** One URL param: `kid` ascending, `-kid` descending. Anything unknown is the default, `due`. */
const parseSort = (raw: string | null): string => {
  const key = raw?.replace(/^-/, '') as SortKey
  return SORT_KEYS.includes(key) ? raw! : 'due'
}

function useSort(): { sort: SortKey; dir: SortDir; onSort: (col: SortKey) => void } {
  const [raw, setRaw] = useUrlState('sort', parseSort, 'due')
  const sort = raw.replace(/^-/, '') as SortKey
  const dir: SortDir = raw.startsWith('-') ? 'desc' : 'asc'
  const onSort = (col: SortKey) => setRaw(col === sort && dir === 'asc' ? `-${col}` : col)
  return { sort, dir, onSort }
}

/** Compare two possibly-empty strings; empty values always sort last whatever the direction. */
function compare(a: string, b: string, dir: SortDir): number {
  if (!a && !b) return 0
  if (!a) return 1
  if (!b) return -1
  const c = a.localeCompare(b)
  return dir === 'asc' ? c : -c
}

function sortRows<T>(rows: T[], key: (r: T) => string, tiebreak: (r: T) => string, dir: SortDir): T[] {
  return [...rows].sort((x, y) => compare(key(x), key(y), dir) || compare(tiebreak(x), tiebreak(y), 'asc'))
}

interface SortHeadProps {
  col: SortKey
  label: string
  sort: SortKey
  dir: SortDir
  onSort: (col: SortKey) => void
}

function SortHead({ col, label, sort, dir, onSort }: SortHeadProps) {
  const active = sort === col
  return (
    <th scope="col" aria-sort={active ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}>
      <button type="button" className="sort-btn" aria-label={`Sort by ${label}`} onClick={() => onSort(col)}>
        {label}
        <span aria-hidden="true">{active ? (dir === 'asc' ? ' ▲' : ' ▼') : ''}</span>
      </button>
    </th>
  )
}

function useCheckOff(setError: (msg: string) => void) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (row: OverviewItem) => updateItem(row.itemId as number, { completed: true }),
    onMutate: async (row: OverviewItem) => {
      setError('')
      await client.cancelQueries({ queryKey: OVERVIEW_KEY })
      const snapshot = client.getQueryData<Overview>(OVERVIEW_KEY)
      if (snapshot) {
        client.setQueryData<Overview>(OVERVIEW_KEY, {
          ...snapshot,
          items: snapshot.items.map(i => (i.id === row.id ? { ...i, status: 'done' as const } : i)),
        })
      }
      return { snapshot }
    },
    onError: (_err, row, ctx) => {
      if (ctx?.snapshot) client.setQueryData(OVERVIEW_KEY, ctx.snapshot)
      setError(`Could not mark "${row.title || 'Untitled'}" done. It has been restored.`)
    },
    onSuccess: () => client.invalidateQueries({ queryKey: OVERVIEW_KEY }),
  })
}

interface FilterBarProps {
  data: Overview
  kid: string
  setKid: (k: string) => void
  sources: string[]
  source: string
  setSource: (s: string) => void
  extra?: ReactNode
}

function FilterBar({ data, kid, setKid, sources, source, setSource, extra }: FilterBarProps) {
  return (
    <div className="list__filters">
      <KidFilter data={data} value={kid} onChange={setKid} />
      <label className="list__select">
        <span aria-hidden="true">Source</span>
        <select aria-label="Filter by source" value={source} onChange={e => setSource(e.target.value)}>
          <option value="">All sources</option>
          {sources.map(s => <option key={s} value={s}>{sourceLabel(s)}</option>)}
        </select>
      </label>
      {extra}
    </div>
  )
}

interface TableProps {
  data: Overview
  kind: Exclude<ListKind, 'events'>
  kid: string
  setKid: (k: string) => void
}

function ItemsTable({ data, kind, kid, setKid }: TableProps) {
  const [source, setSource] = useUrlState('source', raw => raw ?? '', '')
  const [show, setShow] = useUrlState('show', raw => (raw === 'late' || raw === 'missing' ? raw : 'all'), 'all')
  const head = useSort()
  const { sort, dir } = head
  const [error, setError] = useState('')
  const checkOff = useCheckOff(setError)

  const ofKind = data.items.filter(i => KIND_STATUSES[kind].includes(i.status))
  const sources = [...new Set(ofKind.map(i => i.source))].sort()
  const rows = sortRows(
    ofKind.filter(i =>
      itemMatchesKid(i, kid) && (!source || i.source === source) && (show === 'all' || i.status === show)),
    r => (sort === 'title' ? r.title : sort === 'due' ? r.due ?? '' : String(r[sort as 'kid' | 'course' | 'source'] ?? '')),
    r => r.title,
    dir,
  )
  const canCheck = (i: OverviewItem) => !i.readonly && i.itemId !== undefined
  const anyCheck = rows.some(canCheck)
  return (
    <>
      <FilterBar
        data={data} kid={kid} setKid={setKid} sources={sources} source={source} setSource={setSource}
        extra={kind === 'missing' && (
          <label className="list__select">
            Show
            <select value={show} onChange={e => setShow(e.target.value as typeof show)}>
              <option value="all">Missing and late</option>
              <option value="missing">Missing only</option>
              <option value="late">Late only</option>
            </select>
          </label>
        )}
      />
      {error && <Banner tone="error">{error}</Banner>}
      {rows.length === 0 ? <p className="list__empty">Nothing here.</p> : (
        <div className="table-scroll">
          <table className="list__table" aria-label={TITLES[kind]}>
            <thead>
              <tr>
                {anyCheck && <th scope="col"><span className="sr-only">Done</span></th>}
                <SortHead col="kid" label="Who" {...head} />
                <SortHead col="title" label="What" {...head} />
                <SortHead col="course" label="Class" {...head} />
                <SortHead col="due" label="Due" {...head} />
                <SortHead col="source" label="Source" {...head} />
              </tr>
            </thead>
            <tbody>
              {rows.map(i => {
                const stale = itemFreshness(i, data.freshness)
                return (
                  <tr key={i.id}>
                    {anyCheck && (
                      <td>
                        {canCheck(i) && (
                          <input
                            type="checkbox"
                            className="list__check"
                            checked={false}
                            aria-label={`Mark done: ${i.title || 'Untitled'}`}
                            onChange={() => checkOff.mutate(i)}
                          />
                        )}
                      </td>
                    )}
                    <td><KidChip name={i.kid} index={kidIndex(data, i.kid)} wide /></td>
                    <td data-col="what" className="list__what">{i.title}</td>
                    <td className="list__wrap">{i.course}</td>
                    <td className="list__nowrap">{formatDay(i.due)}</td>
                    <td className="list__src">
                      <StatusBadge status={i.status === 'done' ? 'upcoming' : i.status} />
                      <span>{sourceLabel(i.source)}</span>
                      {stale && <AsOfTag freshness={stale} />}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}

function EventsTable({ data, kid, setKid }: Omit<TableProps, 'kind'>) {
  const [source, setSource] = useUrlState('source', raw => raw ?? '', '')
  const head = useSort()
  const { sort, dir } = head
  const sources = [...new Set(data.events.map(e => e.source))].sort()
  const key = (e: OverviewEvent): string =>
    sort === 'due' ? `${e.date} ${e.time}` : sort === 'kid' ? e.kids.join(',') : sort === 'title' ? e.title
      : sort === 'source' ? e.source : sort === 'type' ? e.type : ''
  const rows = sortRows(
    data.events.filter(e => eventMatchesKid(e, kid) && (!source || e.source === source)),
    key, e => `${e.date} ${e.time} ${e.title}`, dir,
  )
  return (
    <>
      <FilterBar data={data} kid={kid} setKid={setKid} sources={sources} source={source} setSource={setSource}
        extra={<Link className="list__link" to="/calendar">Week calendar</Link>} />
      {rows.length === 0 ? <p className="list__empty">No upcoming events.</p> : (
        <div className="table-scroll">
          <table className="list__table" aria-label="Events">
            <thead>
              <tr>
                <SortHead col="kid" label="Who" {...head} />
                <SortHead col="title" label="What" {...head} />
                <SortHead col="due" label="When" {...head} />
                <SortHead col="type" label="Type" {...head} />
                <SortHead col="source" label="Source" {...head} />
              </tr>
            </thead>
            <tbody>
              {rows.map((e, n) => (
                <tr key={`${e.date}-${e.time}-${e.title}-${n}`}>
                  <td className="list__chips">
                    {e.kids.length === 0 ? <span>Everyone</span>
                      : e.kids.map(k => <KidChip key={k} name={k} index={kidIndex(data, k)} wide />)}
                  </td>
                  <td data-col="what" className="list__what">{e.title}</td>
                  <td className="list__nowrap">{formatDay(e.date)}{e.time && ` ${formatTime(e.time)}`}</td>
                  <td className="list__wrap">{e.type}</td>
                  <td>{sourceLabel(e.source)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}

export function ListPage({ kind }: { kind: ListKind }) {
  const query = useOverview()
  const [kid, setKid] = useUrlState('kid', kidParser(query.data), '')
  return (
    <OverviewFrame title={TITLES[kind]} query={query}>
      {data => kind === 'events'
        ? <EventsTable data={data} kid={kid} setKid={setKid} />
        : <ItemsTable data={data} kind={kind} kid={kid} setKid={setKid} />}
    </OverviewFrame>
  )
}
