import { Link } from 'react-router-dom'
import { Banner } from '../design/Banner'
import { useUrlState } from '../lib/urlState'
import { formatInstant } from './format'
import { buildKidViews } from './group'
import { MoreSheet } from './MoreSheet'
import { NoteSheet } from './NoteSheet'
import { ReconBanners } from './ReconBanners'
import { ReconHeader } from './ReconHeader'
import { KidSection } from './KidSection'
import { useReconConfig } from './mode'
import { useAssignments, useSummary } from './queries'
import { SetupSheet } from './SetupSheet'
import { Toast } from './Toast'
import { useReconActions } from './useReconActions'
import { getActor } from './identity'
import './recon.css'

export function ReconSkeleton() {
  return (
    <div role="status" aria-label="Loading assignments" className="recon-skel">
      {[0, 1, 2].map(i => <div key={i} className="recon-skel__row" aria-hidden="true" />)}
    </div>
  )
}

/** The family's daily screen: what to do tonight, what is waiting on a teacher, and what needs action. */
export function Reconcile() {
  const configQuery = useReconConfig()
  const config = configQuery.data ?? undefined
  const summary = useSummary()
  const list = useAssignments()
  const act = useReconActions(config)
  const roster = config?.kids ?? summary.data?.kids.map(k => ({ name: k.name, color: '' })) ?? []
  const [kid, setKid] = useUrlState('kid', raw => (raw && roster.some(k => k.name === raw) ? raw : ''), '')
  const role = getActor()?.role ?? 'parent'
  const ready = summary.data !== undefined && list.data !== undefined
  // A 404 from a switched-off v2 must not flash an error before the config refetch sends the page back to Today.
  const failed = !ready && (summary.isError || list.isError) && configQuery.isFetchedAfterMount

  const views = summary.data && list.data
    ? buildKidViews({ config, summary: summary.data, items: list.data.items, kid })
    : []
  const retry = () => { void summary.refetch(); void list.refetch() }
  const rows = { role, disabled: config?.write_auth === 'unconfigured', showKid: kid === '', statusOf: act.statusOf, onAction: act.onAction }
  const { sheet } = act

  return (
    <main className="screen recon" data-recon-ready={ready || undefined}>
      <div className="recon__top">
        <h1 className="page-title">Reconcile</h1>
        <Link className="recon__older" to="/today">Older view</Link>
      </div>
      {summary.data && <ReconBanners summary={summary.data} />}
      {config?.write_auth === 'unconfigured' && (
        <Banner tone="info">Changes are turned off: writes are not set up on the server.</Banner>
      )}
      {summary.data && <p className="recon__asof">Data as of {formatInstant(summary.data.data_as_of)}</p>}
      {failed && (
        <div className="frame__error">
          <Banner tone="error">Could not load the latest assignments.</Banner>
          <button type="button" className="btn" onClick={retry}>Retry</button>
        </div>
      )}
      {list.data && list.data.total > list.data.items.length && (
        <Banner tone="warn">{`Showing the first ${list.data.items.length} of ${list.data.total} items`}</Banner>
      )}
      {!ready && !failed && <ReconSkeleton />}
      {ready && (
        <>
          <ReconHeader views={views} roster={roster} kid={kid} onKid={setKid} />
          <div className="recon__kids-list">
            {views.map(v => <KidSection key={v.name} view={v} rows={rows} />)}
          </div>
        </>
      )}
      {act.toast && <Toast key={act.toast.id} text={act.toast.text} onUndo={act.toast.undo} onDismiss={act.dismissToast} />}
      {sheet?.type === 'setup' && <SetupSheet needs={sheet.needs} opener={sheet.opener} onClose={act.closeSheet} />}
      {sheet?.type === 'note' && (
        <NoteSheet
          item={sheet.item} draft={sheet.draft} actor={sheet.actor} opener={sheet.opener} onClose={act.closeSheet}
          onResolved={msg => { act.closeSheet(); act.showToast(msg) }}
        />
      )}
      {sheet?.type === 'more' && (
        <MoreSheet
          item={sheet.item} role={role} opener={sheet.opener} onClose={act.closeSheet}
          requireWrite={() => act.requireWrite(sheet.opener)}
          onConfirmed={item => { act.replace(item); act.closeSheet(); act.showToast(`Recorded: teacher confirmed ${item.title}`) }}
        />
      )}
    </main>
  )
}
