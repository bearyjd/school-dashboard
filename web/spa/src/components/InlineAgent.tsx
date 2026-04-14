import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useInlineChat } from '../hooks/useInlineChat'
import { updateItem, createItem, triggerSync } from '../api'
import type { InlineAgentAction } from '../api/types'

interface InlineAgentProps {
  contextType: string
  contextId: string
  suggestions?: string[]
}

const SYNC_TOKEN = (window as unknown as { __SYNC_TOKEN__?: string }).__SYNC_TOKEN__ ?? ''

export function InlineAgent({ contextType, contextId, suggestions = [] }: InlineAgentProps) {
  const [open, setOpen] = useState(false)
  const [input, setInput] = useState('')
  const { loading, reply, action, error, ask, reset } = useInlineChat(contextType, contextId)
  const qc = useQueryClient()

  const send = (msg: string) => {
    setInput('')
    ask(msg)
  }

  const executeAction = async (act: InlineAgentAction) => {
    try {
      if (act.type === 'mark_item_done') {
        await updateItem(act.payload.id as number, { completed: true })
        qc.invalidateQueries({ queryKey: ['items'] })
        qc.invalidateQueries({ queryKey: ['dashboard'] })
      } else if (act.type === 'reschedule_item') {
        await updateItem(act.payload.id as number, { due_date: act.payload.due_date as string })
        qc.invalidateQueries({ queryKey: ['items'] })
      } else if (act.type === 'create_item') {
        await createItem({
          child: act.payload.child as string,
          title: act.payload.title as string,
          type: act.payload.type as string | undefined,
          due_date: act.payload.due_date as string | undefined,
        })
        qc.invalidateQueries({ queryKey: ['items'] })
      } else if (act.type === 'trigger_sync' && SYNC_TOKEN) {
        await triggerSync(act.payload.source as string, 'none', SYNC_TOKEN)
        qc.invalidateQueries({ queryKey: ['syncMeta'] })
      }
    } catch {
      // action failed; leave UI open so user sees current state
    }
    reset()
    setOpen(false)
  }

  if (!open) {
    return (
      <button
        className="btn btn--ghost"
        style={{ fontSize: '12px', padding: '4px 10px', marginTop: 4, alignSelf: 'flex-start' }}
        onClick={() => setOpen(true)}
      >
        ✦ Ask
      </button>
    )
  }

  return (
    <div style={{
      marginTop: 8,
      padding: '10px 12px',
      background: 'rgba(99,102,241,0.06)',
      borderRadius: 8,
      border: '1px solid var(--color-border)',
    }}>
      {/* Input state */}
      {!reply && !loading && (
        <>
          {suggestions.length > 0 && (
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 8 }}>
              {suggestions.map(s => (
                <button
                  key={s}
                  className="btn btn--secondary"
                  style={{ fontSize: '11px', padding: '3px 8px' }}
                  onClick={() => send(s)}
                >
                  {s}
                </button>
              ))}
            </div>
          )}
          <div style={{ display: 'flex', gap: 6 }}>
            <input
              style={{
                flex: 1,
                fontSize: '13px',
                padding: '6px 10px',
                border: '1px solid var(--color-border)',
                borderRadius: 6,
                background: 'var(--color-surface)',
                color: 'var(--color-text)',
                outline: 'none',
              }}
              placeholder="Ask about this…"
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && input.trim() && send(input.trim())}
              autoFocus
            />
            <button
              className="btn btn--primary"
              style={{ padding: '6px 12px', fontSize: '13px' }}
              disabled={!input.trim()}
              onClick={() => send(input.trim())}
            >→</button>
            <button
              className="btn btn--ghost"
              style={{ padding: '6px 10px', fontSize: '13px' }}
              onClick={() => { setOpen(false); reset() }}
            >✕</button>
          </div>
        </>
      )}

      {/* Loading state */}
      {loading && (
        <div style={{ fontSize: '13px', color: 'var(--color-text-muted)' }}>Thinking…</div>
      )}

      {/* Error state */}
      {error && (
        <div style={{ fontSize: '13px', color: 'var(--color-error)' }}>
          {error}
          <button className="btn btn--ghost" style={{ marginLeft: 8, fontSize: '12px' }}
            onClick={() => { reset(); }}>Retry</button>
        </div>
      )}

      {/* Reply state */}
      {reply && (
        <div>
          <div style={{ fontSize: '13px', lineHeight: 1.5, marginBottom: action ? 8 : 0 }}>{reply}</div>
          {action && (
            <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
              <button
                className="btn btn--primary"
                style={{ fontSize: '12px', padding: '5px 12px' }}
                onClick={() => executeAction(action)}
              >
                {action.type === 'mark_item_done' ? '✓ Mark Done' :
                 action.type === 'reschedule_item' ? '📅 Reschedule' :
                 action.type === 'trigger_sync' ? '⟳ Sync Now' : 'Do It'}
              </button>
              <button className="btn btn--ghost" style={{ fontSize: '12px', padding: '5px 10px' }}
                onClick={() => reset()}>Dismiss</button>
            </div>
          )}
          {!action && (
            <button className="btn btn--ghost"
              style={{ fontSize: '12px', padding: '3px 8px', marginTop: 6 }}
              onClick={() => reset()}>✕ Close</button>
          )}
        </div>
      )}
    </div>
  )
}
