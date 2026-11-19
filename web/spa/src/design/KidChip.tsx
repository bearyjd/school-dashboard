import './design.css'

const KID_COLORS = ['blue', 'green', 'lavender', 'amber', 'rose', 'teal'] as const

interface KidChipProps {
  name: string
  /** Named palette colour from the API; anything unknown renders as the first palette colour. */
  color: string
  wide?: boolean
}

export function KidChip({ name, color, wide = false }: KidChipProps) {
  const slot = (KID_COLORS as readonly string[]).includes(color) ? color : KID_COLORS[0]
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
