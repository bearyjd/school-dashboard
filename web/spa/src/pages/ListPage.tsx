import { useMutation, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { useRef, useState, type ReactNode } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { ApiError, updateItem } from '../api'
import { ERROR_MAINTENANCE, ERROR_OFFLINE } from '../api/errors'
import type { Overview, OverviewEvent, OverviewItem, OverviewStatus } from '../api/types'
import { Banner } from '../design/Banner'
import { KidChip } from '../design/KidChip'
import { StatusBadge } from '../design/StatusBadge'
import { Tabs } from '../design/Tabs'
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
const ITEM_SORTS: SortKey[] = ['kid', 'title', 'course', 'due', 'source']
const EVENT_SORTS: SortKey[] = ['kid', 'title', 'due', 'type', 'source']

/** One URL param: `kid` ascending, `-kid` descending. A key this table has no column for is the default, `due`. */
function useSort(allowed: SortKey[]): { sort: SortKey; dir: SortDir; onSort: (col: SortKey) => void } {
  const parseSort = (raw: string | null): string =>
    raw !== null && allowed.includes(raw.replace(/^-/, '') as SortKey) ? raw : 'due'
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

const MAINTENANCE_MESSAGE = 'Updates are paused for maintenance. Try again later.'
const OFFLINE_MESSAGE = "You appear to be offline. Try again when you're connected."

/** Server maintenance is a 503 `{error:'maintenance'}`; the service worker's offline reply is a 503 `{error:'offline'}`. */
function checkOffError(err: unknown, title: string): string {
  if (err instanceof ApiError && err.status === 503 && err.message === ERROR_MAINTENANCE) return MAINTENANCE_MESSAGE
  if (!(err instanceof ApiError) || (err.status === 503 && err.message === ERROR_OFFLINE)) return OFFLINE_MESSAGE
  return `Could not mark "${title || 'Untitled'}" done. It has been restored.`
}

/** Set one row's status in the cached overview; other rows (and other in-flight check-offs) are untouched. */
function setRowStatus(client: QueryClient, id: string, status: OverviewStatus) {
  client.setQueriesData<Overview>({ queryKey: OVERVIEW_KEY }, old =>
    old && { ...old, items: old.items.map(i => (i.id === id ? { ...i, status } : i)) })
}

interface CheckOffMessages {
  setError: (msg: string) => void
  setAnnounce: (msg: string) => void
}

function useCheckOff({ setError, setAnnounce }: CheckOffMessages) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (row: OverviewItem) => updateItem(row.itemId as number, { completed: true }),
    onMutate: async (row: OverviewItem) => {
      setError('')
      await client.cancelQueries({ queryKey: OVERVIEW_KEY })
      setRowStatus(client, row.id, 'done')
    },
    onError: (err, row) => {
      setRowStatus(client, row.id, row.status) // only the failed row comes back
      setError(checkOffError(err, row.title))
    },
    onSuccess: (_data, row) => setAnnounce(`Marked ${row.title || 'Untitled'} done`),
    // Not returned: the mutation must not wait for the refetch (it would delay the focus move on a slow server).
    onSettled: () => { void client.invalidateQueries({ queryKey: OVERVIEW_KEY }) },
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
  const head = useSort(ITEM_SORTS)
  const { sort, dir } = head
  const [error, setError] = useState('')
  const [announce, setAnnounce] = useState('')
  const tableRef = useRef<HTMLTableElement>(null)
  const checkOff = useCheckOff({ setError, setAnnounce })

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
  /** Check a row off, then (once the server agrees) focus the next checkbox, or the table if there is none. */
  const check = (row: OverviewItem) => {
    const next = rows.slice(rows.indexOf(row) + 1).find(canCheck)
    checkOff.mutate(row, {
      onSuccess: () => {
        const box = next && document.querySelector<HTMLInputElement>(`input[data-item-id="${CSS.escape(next.id)}"]`)
        ;(box || tableRef.current || document.getElementById('main'))?.focus()
      },
    })
  }
  return (
    <>
      <div role="status" className="sr-only">{announce}</div>
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
          <table ref={tableRef} tabIndex={-1} className="list__table" aria-label={TITLES[kind]}>
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
                            data-item-id={i.id}
                            aria-label={`Mark done: ${i.title || 'Untitled'}`}
                            onChange={() => check(i)}
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

interface EventsTableProps extends Omit<TableProps, 'kind'> {
  past: boolean
  setPast: (past: boolean) => void
}

function EventsTable({ data, kid, setKid, past, setPast }: EventsTableProps) {
  const [source, setSource] = useUrlState('source', raw => raw ?? '', '')
  const head = useSort(EVENT_SORTS)
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
        extra={(
          <>
            <label className="list__select">
              <input type="checkbox" checked={past} onChange={e => setPast(e.target.checked)} />
              Show past seasons
            </label>
            <Link className="list__link" to="/calendar">Week calendar</Link>
          </>
        )} />
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
                    {e.kids.length === 0 ? <span>{e.unassigned ? 'Unassigned' : 'Everyone'}</span>
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

const TAB_ITEMS = (Object.keys(TITLES) as ListKind[]).map(id => ({ id, label: TITLES[id] }))

/** Switch lists; only ?kid= carries over (sort and source filters are per list). */
function ListTabs({ kind }: { kind: ListKind }) {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const kid = params.get('kid')
  return (
    <Tabs
      items={TAB_ITEMS}
      active={kind}
      idPrefix="list"
      onChange={id => navigate(kid ? `/${id}?kid=${encodeURIComponent(kid)}` : `/${id}`, { replace: true })}
    />
  )
}

export function ListPage({ kind }: { kind: ListKind }) {
  const [pastParam, setPastParam] = useUrlState('past', raw => (raw === '1' ? '1' : ''), '')
  const past = kind === 'events' && pastParam === '1'
  const query = useOverview(past)
  const [kid, setKid] = useUrlState('kid', kidParser(query.data), '')
  return (
    <OverviewFrame
      title={TITLES[kind]}
      query={query}
      header={<ListTabs kind={kind} />}
      panel={{ id: `list-panel-${kind}`, labelledBy: `list-tab-${kind}` }}
    >
      {data => kind === 'events'
        ? <EventsTable data={data} kid={kid} setKid={setKid} past={past} setPast={p => setPastParam(p ? '1' : '')} />
        : <ItemsTable data={data} kind={kind} kid={kid} setKid={setKid} />}
    </OverviewFrame>
  )
}
