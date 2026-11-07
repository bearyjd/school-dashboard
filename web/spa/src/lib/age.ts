/** Human wording for a data age in hours: "just now", "5h ago", "1d 6h ago". */
export function formatAge(hours: number | null): string {
  if (hours === null || !Number.isFinite(hours)) return 'unknown age'
  if (hours < 1) return 'just now'
  const whole = Math.floor(hours)
  if (whole < 24) return `${whole}h ago`
  return `${Math.floor(whole / 24)}d ${whole % 24}h ago`
}
