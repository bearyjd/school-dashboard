import './design.css'

const PALETTE_SIZE = 3

interface KidChipProps {
  name: string
  index: number
  wide?: boolean
}

export function KidChip({ name, index, wide = false }: KidChipProps) {
  const slot = ((Math.trunc(index) % PALETTE_SIZE) + PALETTE_SIZE) % PALETTE_SIZE
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
