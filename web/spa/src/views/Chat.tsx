import { useState, useRef, useEffect } from 'react'
import { sendChat } from '../api'
import { parseToolCalls, needsConfirmation, executeTool } from '../agent/tools'
import { ActionCard } from '../components/ActionCard'
import type { ChatMessage } from '../api/types'
import type { ToolCall } from '../agent/tools'

const QUICK_ACTIONS = [
  "What's due today?",
  "Check homework",
  "Sync IXL",
  "Any GC games this week?",
]

interface Message {
  role: 'user' | 'assistant' | 'system'
  content: string
  toolCall?: ToolCall
  pending?: boolean
}

export function Chat() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [pendingTool, setPendingTool] = useState<{ toolCall: ToolCall; resolve: (confirmed: boolean) => void } | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const confirmTool = (toolCall: ToolCall): Promise<boolean> =>
    new Promise(resolve => setPendingTool({ toolCall, resolve }))

  const send = async (text: string) => {
    if (!text.trim() || loading) return
    const userMsg: Message = { role: 'user', content: text }
    setMessages(prev => [...prev, userMsg])
    setInput('')
    setLoading(true)

    try {
      const history: ChatMessage[] = [...messages, userMsg]
        .filter(m => m.role === 'user' || m.role === 'assistant')
        .map(m => ({ role: m.role as 'user' | 'assistant', content: m.content }))

      const { reply } = await sendChat(text, history)
      const { clean, calls } = parseToolCalls(reply)

      if (clean) {
        setMessages(prev => [...prev, { role: 'assistant', content: clean }])
      }

      for (const toolCall of calls) {
        let confirmed = true
        if (needsConfirmation(toolCall.name)) {
          setMessages(prev => [...prev, { role: 'assistant', content: '', toolCall, pending: true }])
          confirmed = await confirmTool(toolCall)
          setPendingTool(null)
          setMessages(prev => prev.filter((_, i) => i !== prev.length - 1))
        }

        if (confirmed) {
          const result = await executeTool(toolCall)
          setMessages(prev => [...prev, { role: 'system', content: `✓ ${result}` }])
          const followup = await sendChat(`Tool result: ${result}`, [...history, { role: 'assistant', content: clean }])
          setMessages(prev => [...prev, { role: 'assistant', content: followup.reply }])
        } else {
          setMessages(prev => [...prev, { role: 'system', content: '✗ Cancelled' }])
        }
      }
    } catch (e: unknown) {
      setMessages(prev => [...prev, { role: 'system', content: `Error: ${e instanceof Error ? e.message : 'failed'}` }])
    } finally {
      setLoading(false)
    }
  }

  const handleVoice = () => {
    const SR = (window as unknown as { webkitSpeechRecognition?: new() => SpeechRecognition }).webkitSpeechRecognition
      || (window as unknown as { SpeechRecognition?: new() => SpeechRecognition }).SpeechRecognition
    if (!SR) return
    const rec = new SR()
    rec.onresult = (e: SpeechRecognitionEvent) => { setInput(e.results[0][0].transcript) }
    rec.start()
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100dvh', paddingBottom: 'var(--nav-height)' }}>
      {/* Quick actions */}
      <div style={{ display: 'flex', gap: 'var(--space-sm)', padding: 'var(--space-sm) var(--space-md)', overflowX: 'auto', flexShrink: 0, borderBottom: '1px solid var(--color-border)' }}>
        {QUICK_ACTIONS.map(q => (
          <button key={q} className="btn btn--secondary" style={{ whiteSpace: 'nowrap', padding: '6px 14px', fontSize: '13px' }}
            onClick={() => send(q)}>{q}</button>
        ))}
      </div>

      {/* Messages */}
      <div style={{ flex: 1, overflowY: 'auto', padding: 'var(--space-md)', display: 'flex', flexDirection: 'column', gap: 'var(--space-md)' }}>
        {messages.length === 0 && (
          <div style={{ textAlign: 'center', color: 'var(--color-text-muted)', marginTop: 'var(--space-xl)' }}>
            Ask about homework, grades, or upcoming events.<br />I can also take actions — try "Mark Ford's math done."
          </div>
        )}
        {messages.map((msg, i) => (
          <div key={i}>
            {msg.toolCall ? (
              pendingTool?.toolCall === msg.toolCall ? (
                <ActionCard
                  toolCall={msg.toolCall}
                  onConfirm={() => pendingTool.resolve(true)}
                  onCancel={() => pendingTool.resolve(false)}
                />
              ) : null
            ) : (
              <div style={{
                alignSelf: msg.role === 'user' ? 'flex-end' : 'flex-start',
                maxWidth: '85%',
                background: msg.role === 'user' ? 'var(--color-accent)' : msg.role === 'system' ? 'var(--color-surface2)' : 'var(--color-surface)',
                borderRadius: msg.role === 'user' ? 'var(--radius-md) var(--radius-md) 4px var(--radius-md)' : 'var(--radius-md) var(--radius-md) var(--radius-md) 4px',
                padding: '10px 14px',
                fontSize: '14px',
                color: msg.role === 'system' ? 'var(--color-text-muted)' : 'var(--color-text)',
                whiteSpace: 'pre-wrap',
              }}>{msg.content}</div>
            )}
          </div>
        ))}
        {loading && <div style={{ color: 'var(--color-text-muted)', fontSize: '13px' }}>Thinking…</div>}
        <div ref={bottomRef} />
      </div>

      {/* Input bar */}
      <div style={{ display: 'flex', gap: 'var(--space-sm)', padding: 'var(--space-sm) var(--space-md)', borderTop: '1px solid var(--color-border)', flexShrink: 0 }}>
        <button className="btn btn--ghost" style={{ fontSize: '20px', padding: '0 8px' }} onClick={handleVoice} title="Voice input">🎤</button>
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && !e.shiftKey && send(input)}
          placeholder="Ask anything…"
          style={{ flex: 1 }}
        />
        <button className="btn btn--primary" disabled={loading || !input.trim()} onClick={() => send(input)}>
          Send
        </button>
      </div>
    </div>
  )
}
