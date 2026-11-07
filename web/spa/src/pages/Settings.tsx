import { useState } from 'react'
import { Link } from 'react-router-dom'
import { getStoredTheme, setTheme, type ThemeChoice } from '../design/theme'
import './Settings.css'

const CHOICES: { id: ThemeChoice; label: string; hint: string }[] = [
  { id: 'midnight', label: 'Midnight', hint: 'Dark' },
  { id: 'daylight', label: 'Daylight', hint: 'Light' },
  { id: 'system', label: 'System', hint: 'Follow this device' },
]

export function Settings() {
  const [choice, setChoice] = useState<ThemeChoice>(getStoredTheme)

  const pick = (next: ThemeChoice) => {
    setTheme(next)
    setChoice(next)
  }

  return (
    <main className="screen">
      <h1 className="page-title">Settings</h1>

      <fieldset className="settings__group">
        <legend>Theme</legend>
        {CHOICES.map(c => (
          <label key={c.id} className="settings__option">
            <input
              type="radio"
              name="theme"
              value={c.id}
              checked={choice === c.id}
              onChange={() => pick(c.id)}
            />
            <span className="settings__label">{c.label}</span>
            <span className="settings__hint">{c.hint}</span>
          </label>
        ))}
      </fieldset>

      <section className="settings__group" aria-labelledby="settings-more">
        <h2 id="settings-more" className="settings__legend">More</h2>
        <p><Link className="list__link" to="/sync">Sync sources</Link></p>
        <p><Link className="list__link" to="/classic/home">Classic view</Link></p>
      </section>
    </main>
  )
}
