import { Link } from 'react-router-dom'
import type { Need } from './access'
import { Sheet } from './Sheet'

interface Props {
  needs: Need[]
  opener: HTMLElement | null
  onClose: () => void
}

/** Shown instead of calling the API when this device has no name or write token yet. */
export function SetupSheet({ needs, opener, onClose }: Props) {
  return (
    <Sheet label="Set up this device" opener={opener} onClose={onClose}>
      <h2 className="recon-sheet__title">Set up this device</h2>
      {needs.includes('name') && <p>Add your name in Settings so changes show who made them.</p>}
      {needs.includes('token') && <p>Add the write token in Settings. Ask the owner for it.</p>}
      <div className="recon-sheet__actions">
        <button type="button" className="recon-btn" onClick={onClose}>Close</button>
        <Link className="recon-btn recon-btn--primary" to="/settings">Open Settings</Link>
      </div>
    </Sheet>
  )
}
