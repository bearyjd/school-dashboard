import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import type { Overview, OverviewEvent, OverviewItem, OverviewStatus } from '../api/types'
import { ItemRow } from '../components/ItemRow'
import { KidFilter, OverviewFrame } from '../components/OverviewFrame'
import { KidChip } from '../design/KidChip'
import { Tile } from '../design/Tile'
import { useOverview } from '../lib/overview'
import { useUrlState } from '../lib/urlState'
import { APP_TZ, eventMatchesKid, formatDay, formatTime, itemMatchesKid, kidIndex, kidParser } from '../lib/view'
import { localToday, todayAnchor, weekDays } from '../lib/week'
import './Today.css'

const COMING_UP_LIMIT = 8
const WEEK_PANEL_LIMIT = 6

function byStatus(items: OverviewItem[], status: OverviewStatus): OverviewItem[] {
  return items.filter(i => i.status === status)
}

function ItemList({ items, data }: { items: OverviewItem[]; data: Overview }) {
  return (
    <ul className="today__list">
      {items.map(i => <li key={i.id}><ItemRow item={i} data={data} /></li>)}
    </ul>
  )
}

function WeekPanel({ events, data }: { events: OverviewEvent[]; data: Overview }) {
  return (
    <section className="today__panel" aria-labelledby="week-h">
      <h2 id="week-h" className="today__h">This week</h2>
      {events.length === 0 ? <p className="today__muted">No events in the next 7 days.</p> : (
        <ul className="today__events">
          {events.slice(0, WEEK_PANEL_LIMIT).map((e, i) => (
            <li key={`${e.date}-${e.title}-${i}`} className="today__event">
              <span className="today__when">{formatDay(e.date)}{e.time && ` ${formatTime(e.time)}`}</span>
              <span className="today__event-title">{e.title}</span>
              {e.kids.map(k => <KidChip key={k} name={k} index={kidIndex(data, k)} />)}
            </li>
          ))}
        </ul>
      )}
      <Link className="today__link" to="/calendar">Open calendar</Link>
    </section>
  )
}

function TodayBody({ data, kid, setKid }: { data: Overview; kid: string; setKid: (k: string) => void }) {
  const navigate = useNavigate()
  const [showMissing, setShowMissing] = useState(false)
  const items = data.items.filter(i => itemMatchesKid(i, kid))
  const now = new Date()
  const today = localToday(now, APP_TZ)
  const week = new Set(weekDays(todayAnchor(now, APP_TZ), APP_TZ, now).map(d => d.date))
  const events = data.events.filter(e => week.has(e.date) && eventMatchesKid(e, kid))
  const overdue = byStatus(items, 'overdue')
  // Same set as the /missing page: PowerSchool missing plus late (late rows carry a "late" badge).
  const missing = items.filter(i => i.status === 'missing' || i.status === 'late')
  const tonight = byStatus(items, 'tonight')
  const upcoming = byStatus(items, 'upcoming').filter(i => !i.due || i.due >= today)
  const next = upcoming.find(i => i.due)
  const go = (path: string) => () => navigate(kid ? `${path}?kid=${encodeURIComponent(kid)}` : path)
  return (
    <>
      <section aria-label="Summary" className="today__tiles">
        <div className="today__tile today__tile--hero"><Tile label="Due tonight" value={tonight.length} tone="tonight" onClick={go('/tonight')} /></div>
        <div className="today__tile"><Tile label="Overdue" value={overdue.length} tone="overdue" onClick={go('/overdue')} /></div>
        <div className="today__tile"><Tile label="Missing" value={missing.length} tone="missing" onClick={go('/missing')} /></div>
        <div className="today__tile"><Tile label="Events this week" value={events.length} tone="ok" onClick={go('/events')} /></div>
      </section>
      <KidFilter data={data} value={kid} onChange={setKid} />
      <div className="today__cols">
        <div className="today__main">
          {overdue.length > 0 && (
            <section aria-labelledby="overdue-h">
              <h2 id="overdue-h" className="today__h">Overdue ({overdue.length})</h2>
              <ItemList items={overdue} data={data} />
            </section>
          )}
          <section aria-labelledby="tonight-h">
            <h2 id="tonight-h" className="today__h">Due tonight ({tonight.length})</h2>
            {tonight.length > 0 ? <ItemList items={tonight} data={data} /> : (
              <p className="today__muted">
                Nothing due tonight.{next && <> Next: <strong>{next.title}</strong>, due {formatDay(next.due)}.</>}
              </p>
            )}
          </section>
          {missing.length > 0 && (
            <section aria-labelledby="missing-h">
              <h2 id="missing-h" className="today__h">
                <button type="button" className="today__toggle" aria-expanded={showMissing} onClick={() => setShowMissing(v => !v)}>
                  <span aria-hidden="true">{showMissing ? '▾' : '▸'}</span> Missing ({missing.length})
                </button>
              </h2>
              {showMissing && <ItemList items={missing} data={data} />}
            </section>
          )}
          {upcoming.length > 0 && (
            <section aria-labelledby="up-h">
              <h2 id="up-h" className="today__h">Coming up</h2>
              <ItemList items={upcoming.slice(0, COMING_UP_LIMIT)} data={data} />
            </section>
          )}
        </div>
        <aside className="today__side"><WeekPanel events={events} data={data} /></aside>
      </div>
    </>
  )
}

export function Today() {
  const query = useOverview()
  const [kid, setKid] = useUrlState('kid', kidParser(query.data), '')
  return (
    <OverviewFrame title="Today" query={query}>
      {data => <TodayBody data={data} kid={kid} setKid={setKid} />}
    </OverviewFrame>
  )
}
