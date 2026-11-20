export const KID_COLORS = ['blue', 'green', 'lavender', 'amber', 'rose', 'teal'] as const

/** CSS slot (`kid-<slot>`) for a named API colour; anything unknown is the neutral token. */
export function kidSlot(color: string): string {
  return (KID_COLORS as readonly string[]).includes(color) ? color : 'neutral'
}
