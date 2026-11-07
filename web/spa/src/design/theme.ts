export type ThemeChoice = 'midnight' | 'daylight' | 'system'
export type ResolvedTheme = 'midnight' | 'daylight'

export const THEME_KEY = 'school-theme'
const CHOICES: readonly string[] = ['midnight', 'daylight', 'system']

export function resolveTheme(choice: ThemeChoice, prefersDark: boolean): ResolvedTheme {
  if (choice === 'system') return prefersDark ? 'midnight' : 'daylight'
  return choice
}

export function getStoredTheme(): ThemeChoice {
  try {
    const v = localStorage.getItem(THEME_KEY)
    return v !== null && CHOICES.includes(v) ? (v as ThemeChoice) : 'midnight'
  } catch {
    return 'midnight'
  }
}

function prefersDark(): boolean {
  return typeof window.matchMedia !== 'function' || window.matchMedia('(prefers-color-scheme: dark)').matches
}

export function applyTheme(choice: ThemeChoice): void {
  document.documentElement.dataset.theme = resolveTheme(choice, prefersDark())
}

/** Follow device light/dark changes while the stored choice is 'system'. Returns an unsubscribe function. */
export function watchSystemTheme(): () => void {
  if (typeof window.matchMedia !== 'function') return () => {}
  const mql = window.matchMedia('(prefers-color-scheme: dark)')
  const onChange = () => {
    if (getStoredTheme() === 'system') applyTheme('system')
  }
  mql.addEventListener('change', onChange)
  return () => mql.removeEventListener('change', onChange)
}

export function setTheme(choice: ThemeChoice): void {
  try {
    localStorage.setItem(THEME_KEY, choice)
  } catch {
    /* storage unavailable (private mode): the theme still applies for this visit */
  }
  applyTheme(choice)
}
