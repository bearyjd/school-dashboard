import { useRef, useState } from 'react'
import {
  clearReconToken, DEFAULT_TOKEN_MIN, getActor, getReconToken, maskedToken, setActor, setReconToken, validateActorName,
} from './identity'
import { useReconConfig } from './mode'
import type { Role } from './types'

const ROLES: { id: Role; label: string }[] = [{ id: 'parent', label: 'Parent' }, { id: 'kid', label: 'Kid' }]

/** Settings > Assignments: who is using this device, and the write token for changing assignments. */
export function AssignmentsSettings() {
  const config = useReconConfig().data
  const minLength = config?.write_token_min_length ?? DEFAULT_TOKEN_MIN
  const [name, setName] = useState(() => getActor()?.name ?? '')
  const [role, setRole] = useState<Role | null>(() => getActor()?.role ?? null)
  const [nameNote, setNameNote] = useState('')
  const [tokenInput, setTokenInput] = useState('')
  const [hasToken, setHasToken] = useState(() => getReconToken() !== '')
  const [tokenNote, setTokenNote] = useState('')
  const tokenRef = useRef<HTMLInputElement>(null)

  const saveName = () => {
    const problem = validateActorName(name)
    if (problem) return setNameNote(problem)
    if (!role) return setNameNote('Choose Parent or Kid')
    setNameNote(setActor({ name, role }) === 'saved' ? 'Saved on this device' : 'Kept until you close this tab')
  }

  const saveToken = () => {
    const value = tokenInput.trim()
    if (value.length < minLength) return setTokenNote(`Token must be at least ${minLength} characters`)
    const result = setReconToken(value)
    setTokenInput('')
    setHasToken(getReconToken() !== '')
    setTokenNote(result === 'saved' ? 'Write token saved on this device' : 'Write token kept until you close this tab')
    tokenRef.current?.focus()
  }

  const removeToken = () => {
    clearReconToken()
    setTokenInput('')
    setHasToken(false)
    setTokenNote('Write token cleared')
    tokenRef.current?.focus()
  }

  return (
    <section className="settings__group" aria-labelledby="settings-assign">
      <h2 id="settings-assign" className="settings__legend">Assignments</h2>
      <p className="settings__hint">Needed to mark assignments done or turned in. Kept only in this browser.</p>
      {config?.write_auth === 'open' && <p className="settings__hint">This server does not ask for a write token.</p>}
      {config?.write_auth === 'unconfigured' && <p className="settings__hint">Writes are not set up on the server yet.</p>}

      <form className="settings__token" onSubmit={e => { e.preventDefault(); saveName() }}>
        <label htmlFor="actor-name-input" className="settings__label">Who is using this device</label>
        <input id="actor-name-input" type="text" autoComplete="off" value={name} onChange={e => setName(e.target.value)} />
        <fieldset className="settings__roles">
          <legend className="settings__hint">This device is used by</legend>
          {ROLES.map(r => (
            <label key={r.id} className="settings__option">
              <input type="radio" name="actor-role" value={r.id} checked={role === r.id} onChange={() => setRole(r.id)} />
              <span className="settings__label">{r.label}</span>
            </label>
          ))}
        </fieldset>
        <div className="settings__token-actions">
          <button type="submit" className="btn btn--primary">Save name</button>
        </div>
        <p aria-live="polite" className="settings__hint">{nameNote}</p>
      </form>

      <form className="settings__token" onSubmit={e => { e.preventDefault(); saveToken() }}>
        <label htmlFor="recon-token-input" className="settings__label">Write token</label>
        <input
          id="recon-token-input"
          type="password"
          autoComplete="new-password"
          ref={tokenRef}
          value={tokenInput}
          onChange={e => setTokenInput(e.target.value)}
        />
        <p className="settings__hint">
          Ask the owner for the RECON_WRITE_TOKEN value.{hasToken ? ` A write token is saved on this device (${maskedToken()}).` : ''}
        </p>
        <div className="settings__token-actions">
          <button type="submit" className="btn btn--primary" disabled={!tokenInput.trim()}>Save token</button>
          <button type="button" className="btn btn--secondary" onClick={removeToken} disabled={!hasToken}>Clear token</button>
        </div>
        <p aria-live="polite" className="settings__hint">{tokenNote}</p>
      </form>
    </section>
  )
}
