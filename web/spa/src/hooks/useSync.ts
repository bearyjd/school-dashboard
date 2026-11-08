import { useState, useCallback, useRef, useEffect } from 'react'
import { triggerSync, fetchSyncStatus } from '../api'
import type { SyncStatus } from '../api/types'
import { getSyncToken, MSG_NO_TOKEN, syncErrorMessage } from '../lib/syncToken'

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
        setError(syncErrorMessage(e))
        stopPolling()
      }
    }, 3000)
  }, [stopPolling])

  useEffect(() => () => stopPolling(), [stopPolling])

  const trigger = useCallback(async (sources: string, digest = 'none') => {
    const token = getSyncToken()
    if (!token) { setError(MSG_NO_TOKEN); return }
    setTriggering(true)
    setError(null)
    try {
      await triggerSync(sources, digest, token)
      startPolling()
    } catch (e: unknown) {
      setError(syncErrorMessage(e))
    } finally {
      setTriggering(false)
    }
  }, [startPolling])

  return { status, triggering, error, trigger }
}
