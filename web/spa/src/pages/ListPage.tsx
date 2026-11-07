export type ListKind = 'tonight' | 'overdue' | 'missing' | 'events'

const TITLES: Record<ListKind, string> = {
  tonight: 'Tonight',
  overdue: 'Overdue',
  missing: 'Missing',
  events: 'Events',
}

export function ListPage({ kind }: { kind: ListKind }) {
  return (
    <main className="screen">
      <h1 className="page-title">{TITLES[kind]}</h1>
    </main>
  )
}
