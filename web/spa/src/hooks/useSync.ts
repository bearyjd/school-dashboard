import { useState, useCallback, useRef, useEffect } from 'react'
import { ApiError, triggerSync, fetchSyncStatus } from '../api'
import type { SyncStatus } from '../api/types'
import { getSyncToken, NO_TOKEN, STATUS_FAILED, syncErrorMessage, type SyncError } from '../lib/syncToken'

export function useSync() {
  const [status, setStatus] = useState<SyncStatus | null>(null)
  const [triggering, setTriggering] = useState(false)
  const [error, setError] = useState<SyncError | null>(null)
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
      } catch {
        setError(STATUS_FAILED)
        stopPolling()
      }
    }, 3000)
  }, [stopPolling])

  useEffect(() => () => stopPolling(), [stopPolling])

  const trigger = useCallback(async (sources: string, digest = 'none') => {
    const token = getSyncToken()
    if (!token) { setError(NO_TOKEN); return }
    setTriggering(true)
    setError(null)
    try {
      await triggerSync(sources, digest, token)
      startPolling()
    } catch (e: unknown) {
      // 409: a sync is already running; follow it instead of reporting an error
      if (e instanceof ApiError && e.status === 409) startPolling()
      else setError(syncErrorMessage(e))
    } finally {
      setTriggering(false)
    }
  }, [startPolling])

  return { status, triggering, error, trigger }
}
