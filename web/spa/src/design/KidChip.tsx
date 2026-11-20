import { kidSlot } from '../lib/kidSlot'
import './design.css'

interface KidChipProps {
  name: string
  /** Named palette colour from the API; anything unknown renders in the neutral token. */
  color: string
  wide?: boolean
}

export function KidChip({ name, color, wide = false }: KidChipProps) {
  const slot = kidSlot(color)
  const initial = Array.from(name.trim())[0]?.toUpperCase() ?? '?'
  return (
    <span
      className={`kid-chip kid-${slot}`}
      title={name}
      {...(wide ? {} : { role: 'img', 'aria-label': name || 'Unknown child' })}
    >
      <span className="kid-chip__dot">{initial}</span>
      {wide && <span className="kid-chip__name">{name}</span>}
    </span>
  )
}
