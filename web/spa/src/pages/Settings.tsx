import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { getStoredTheme, setTheme, type ThemeChoice } from '../design/theme'
import { KidChip } from '../design/KidChip'
import { useOverview } from '../lib/overview'
import { clearSyncToken, getSyncToken, setSyncToken } from '../lib/syncToken'
import './Settings.css'

const CHOICES: { id: ThemeChoice; label: string; hint: string }[] = [
  { id: 'midnight', label: 'Midnight', hint: 'Dark' },
  { id: 'daylight', label: 'Daylight', hint: 'Light' },
  { id: 'system', label: 'System', hint: 'Follow this device' },
]

export function Settings() {
  const [choice, setChoice] = useState<ThemeChoice>(getStoredTheme)
  const [tokenInput, setTokenInput] = useState('')
  const [hasToken, setHasToken] = useState(() => getSyncToken() !== '')
  const [tokenNote, setTokenNote] = useState('')
  const tokenRef = useRef<HTMLInputElement>(null)
  const kids = useOverview().data?.kids ?? []

  const pick = (next: ThemeChoice) => {
    setTheme(next)
    setChoice(next)
  }

  const saveToken = () => {
    const result = setSyncToken(tokenInput)
    setTokenInput('')
    setHasToken(getSyncToken() !== '')
    setTokenNote(
      result === 'saved' ? 'Sync token saved on this device'
        : result === 'tab' ? 'Sync token kept until you close this tab'
          : 'Enter the token first',
    )
    tokenRef.current?.focus()
  }

  const removeToken = () => {
    clearSyncToken()
    setTokenInput('')
    setHasToken(getSyncToken() !== '')
    setTokenNote('Sync token cleared')
    tokenRef.current?.focus()
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

      {kids.length > 0 && (
        <section className="settings__group" aria-labelledby="settings-kids">
          <h2 id="settings-kids" className="settings__legend">Kid colours</h2>
          <p className="settings__hint">Colours follow the order of the roster.</p>
          <ul aria-label="Kid colours" className="settings__kids">
            {kids.map(k => (
              <li key={k.name} className="settings__kid">
                <KidChip name={k.name} index={k.colorIndex} wide />
                <span className="settings__hint"> (colour {k.colorIndex + 1})</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="settings__group">
        <h2 id="settings-token" className="settings__legend">Sync access</h2>
        <p className="settings__hint" id="settings-token-help">
          Needed to start a sync. Kept only in this browser, never sent anywhere except the sync request.
          Ask the owner for the SYNC_TOKEN value.{hasToken ? ' A token is saved on this device.' : ''}
        </p>
        <form className="settings__token" onSubmit={e => { e.preventDefault(); saveToken() }}>
          <label htmlFor="sync-token-input" className="settings__label">Sync token</label>
          <input
            id="sync-token-input"
            type="password"
            autoComplete="new-password"
            ref={tokenRef}
            value={tokenInput}
            onChange={e => setTokenInput(e.target.value)}
            aria-describedby="settings-token-help"
          />
          <div className="settings__token-actions">
            <button type="submit" className="btn btn--primary" disabled={!tokenInput.trim()}>Save</button>
            <button type="button" className="btn btn--secondary" onClick={removeToken} disabled={!hasToken}>Clear</button>
          </div>
        </form>
        <p role="status" className="settings__hint">{tokenNote}</p>
      </section>

      <section className="settings__group" aria-labelledby="settings-more">
        <h2 id="settings-more" className="settings__legend">More</h2>
        <p><Link className="list__link" to="/sync">Sync sources</Link></p>
        <p><Link className="list__link" to="/classic/home">Classic view</Link></p>
      </section>
    </main>
  )
}
