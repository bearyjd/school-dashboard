import type { ToolCall } from '../agent/tools'
import { describeToolCall } from '../agent/tools'
import './ActionCard.css'

interface Props {
  toolCall: ToolCall
  onConfirm: () => void
  onCancel: () => void
}

export function ActionCard({ toolCall, onConfirm, onCancel }: Props) {
  return (
    <div className="action-card">
      <div className="action-card__label">Action required</div>
      <div className="action-card__desc">{describeToolCall(toolCall)}</div>
      <div className="action-card__btns">
        <button className="btn btn--ghost" onClick={onCancel}>Cancel</button>
        <button className="btn btn--primary" onClick={onConfirm}>Confirm</button>
      </div>
    </div>
  )
}
