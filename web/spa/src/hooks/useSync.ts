import { useState, useCallback, useRef, useEffect } from 'react'
import { triggerSync, fetchSyncStatus } from '../api'
import type { SyncStatus } from '../api/types'

const SYNC_TOKEN = (window as unknown as { __SYNC_TOKEN__?: string }).__SYNC_TOKEN__ ?? ''

export function useSync() {
  const [status, setStatus] = useState<SyncStatus | null>(null)
  const [triggering, setTriggering] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<number | null>(null)

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }, [])

  const startPolling = useCallback(() => {
    stopPolling()
    pollRef.current = window.setInterval(async () => {
      try {
        const s = await fetchSyncStatus()
        setStatus(s)
        if (!s.running) stopPolling()
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : 'polling failed')
        stopPolling()
      }
    }, 3000)
  }, [stopPolling])

  useEffect(() => () => stopPolling(), [stopPolling])

  const trigger = useCallback(async (sources: string, digest = 'none') => {
    if (!SYNC_TOKEN) { setError('SYNC_TOKEN not configured'); return }
    setTriggering(true)
    setError(null)
    try {
      await triggerSync(sources, digest, SYNC_TOKEN)
      startPolling()
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'sync failed')
    } finally {
      setTriggering(false)
    }
  }, [startPolling])

  return { status, triggering, error, trigger }
}
