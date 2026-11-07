import './design.css'

export type Tone = 'overdue' | 'missing' | 'tonight' | 'ok' | 'neutral'

interface TileProps {
  label: string
  value: number | string
  tone: Tone
  onClick?: () => void
}

export function Tile({ label, value, tone, onClick }: TileProps) {
  const body = (
    <>
      <span className="tile__value">{value}</span>
      <span className="tile__label">{label}</span>
    </>
  )
  const cls = `tile tile--${tone}`
  return onClick ? (
    <button type="button" className={`${cls} tile--action`} onClick={onClick}>{body}</button>
  ) : (
    <div className={cls}>{body}</div>
  )
}
