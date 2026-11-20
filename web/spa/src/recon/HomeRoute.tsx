import { Link } from 'react-router-dom'
import { Today } from '../pages/Today'
import { useReconMode } from './mode'
import { Reconcile, ReconSkeleton } from './Reconcile'

/** `/`: Reconcile when v2 is live, the old Today screen otherwise; a skeleton (no error, no flash) while deciding. */
export function HomeRoute() {
  const mode = useReconMode()
  if (mode === 'loading') return <main className="screen"><ReconSkeleton /></main>
  return mode === 'recon' ? <Reconcile /> : <Today />
}

/** `/recon`: always Reconcile when live; a plain notice when v2 is not switched on. */
export function ReconRoute() {
  const mode = useReconMode()
  if (mode === 'loading') return <main className="screen"><ReconSkeleton /></main>
  if (mode === 'recon') return <Reconcile />
  return (
    <main className="screen">
      <h1 className="page-title">Reconcile</h1>
      <p>Reconcile is not switched on yet.</p>
      <p><Link className="recon__older" to="/today">Older view</Link></p>
    </main>
  )
}
