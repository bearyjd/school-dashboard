import { useState, useCallback } from 'react'
import { askInlineAgent } from '../api'
import type { InlineAgentAction } from '../api/types'

interface State {
  loading: boolean
  reply: string | null
  action: InlineAgentAction | null
  error: string | null
}

const INITIAL: State = { loading: false, reply: null, action: null, error: null }

export function useInlineChat(contextType: string, contextId: string) {
  const [state, setState] = useState<State>(INITIAL)

  const ask = useCallback(async (message: string) => {
    setState({ loading: true, reply: null, action: null, error: null })
    try {
      const res = await askInlineAgent(contextType, contextId, message)
      setState({ loading: false, reply: res.reply, action: res.action, error: null })
    } catch (e: unknown) {
      setState({
        loading: false, reply: null, action: null,
        error: e instanceof Error ? e.message : 'request failed',
      })
    }
  }, [contextType, contextId])

  const reset = useCallback(() => setState(INITIAL), [])

  return { ...state, ask, reset }
}
