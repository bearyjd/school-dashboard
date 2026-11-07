import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import type { Overview } from '../api/types'
import { KidFilter, OverviewFrame } from '../components/OverviewFrame'
import { KidChip } from '../design/KidChip'
import { StatusBadge } from '../design/StatusBadge'
import { useOverview } from '../lib/overview'
import { useUrlState } from '../lib/urlState'
import { APP_TZ, eventMatchesKid, formatTime, itemMatchesKid, kidIndex, kidParser } from '../lib/view'
import { localToday, shiftWeek, weekDays } from '../lib/week'
import './Calendar.css'

const MAX_WEEK_OFFSET = 52
const WIDE_QUERY = '(min-width: 768px)'

function parseWeek(raw: string | null): number {
  const n = Number(raw)
  return raw !== null && Number.isInteger(n) && Math.abs(n) <= MAX_WEEK_OFFSET ? n : 0
}

/** True on tablet/desktop widths. Without matchMedia (old webviews) the grid is assumed. */
function useWide(): boolean {
  const query = () => (typeof window.matchMedia === 'function' ? window.matchMedia(WIDE_QUERY) : null)
  const [wide, setWide] = useState(() => query()?.matches ?? true)
  useEffect(() => {
    const mq = query()
    if (!mq) return
    const onChange = () => setWide(mq.matches)
    onChange()
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])
  return wide
}

interface DayProps {
  data: Overview
  kid: string
  date: string
  label: string
  isToday: boolean
}

function Day({ data, kid, date, label, isToday }: DayProps) {
  const items = data.items.filter(i => i.due === date && i.status !== 'done' && itemMatchesKid(i, kid))
  const events = data.events.filter(e => e.date === date && eventMatchesKid(e, kid))
  const id = `day-${date}`
  return (
    <section
      className={`cal-day${isToday ? ' cal-day--today' : ''}`}
      aria-labelledby={id}
      aria-current={isToday ? 'date' : undefined}
    >
      <h2 id={id} className="cal-day__h">{label}{isToday && <span className="cal-day__today"> Today</span>}</h2>
      {items.length + events.length === 0 && <p className="cal-day__empty">Nothing</p>}
      <ul className="cal-day__list">
        {events.map((e, n) => (
          <li key={`e-${n}-${e.title}`} className="cal-entry cal-entry--event">
            <span className="cal-entry__kind">Event{e.time && ` ${formatTime(e.time)}`}</span>
            <span className="cal-entry__title">{e.title}</span>
            <span className="cal-entry__chips">
              {e.kids.length === 0 ? <span className="cal-entry__all">Everyone</span>
                : e.kids.map(k => <KidChip key={k} name={k} index={kidIndex(data, k)} />)}
            </span>
          </li>
        ))}
        {items.map(i => (
          <li key={i.id} className="cal-entry cal-entry--item">
            <span className="cal-entry__kind">Due</span>
            <span className="cal-entry__title">{i.title}</span>
            <span className="cal-entry__chips">
              <KidChip name={i.kid} index={kidIndex(data, i.kid)} />
              <StatusBadge status={i.status === 'done' ? 'upcoming' : i.status} />
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}

function CalendarBody({ data, kid, setKid }: { data: Overview; kid: string; setKid: (k: string) => void }) {
  const [offset, setOffset] = useUrlState('week', parseWeek, 0)
  const wide = useWide()
  const now = new Date()
  const base = new Date(`${localToday(now, APP_TZ)}T12:00:00Z`)
  const days = weekDays(shiftWeek(base, offset, APP_TZ), APP_TZ, now)
  const mine = data.items.filter(i => itemMatchesKid(i, kid))
  const overdue = mine.filter(i => i.status === 'overdue').length
  const missing = mine.filter(i => i.status === 'missing').length
  return (
    <>
      {(overdue > 0 || missing > 0) && (
        <p className="cal__backlog" role="note">
          <span aria-hidden="true">!</span> Not on the calendar:{' '}
          <Link to="/overdue">{overdue} overdue</Link>{' and '}<Link to="/missing">{missing} missing</Link>
        </p>
      )}
      <KidFilter data={data} value={kid} onChange={setKid} />
      <div className="cal__nav">
        <button type="button" className="btn" onClick={() => setOffset(offset - 1)}>
          <span aria-hidden="true">◀ </span>Previous week
        </button>
        <button type="button" className="btn" onClick={() => setOffset(0)} disabled={offset === 0}>This week</button>
        <button type="button" className="btn" onClick={() => setOffset(offset + 1)}>
          Next week<span aria-hidden="true"> ▶</span>
        </button>
      </div>
      <div className={`cal cal--${wide ? 'grid' : 'agenda'}`} data-testid="calendar-layout" data-layout={wide ? 'grid' : 'agenda'}>
        {days.map(d => <Day key={d.date} data={data} kid={kid} {...d} />)}
      </div>
    </>
  )
}

export function Calendar() {
  const query = useOverview()
  const [kid, setKid] = useUrlState('kid', kidParser(query.data), '')
  return (
    <OverviewFrame title="Calendar" query={query}>
      {data => <CalendarBody data={data} kid={kid} setKid={setKid} />}
    </OverviewFrame>
  )
}
