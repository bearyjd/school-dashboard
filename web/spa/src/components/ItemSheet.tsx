import { useState } from 'react'
import type { Item } from '../api/types'
import './ItemSheet.css'

interface Props {
  item: Item | null
  onClose: () => void
  onSave: (updates: Partial<Item>) => void
  onDelete?: () => void
}

export function ItemSheet({ item, onClose, onSave, onDelete }: Props) {
  const [title, setTitle] = useState(item?.title ?? '')
  const [dueDate, setDueDate] = useState(item?.due_date ?? '')
  const [notes, setNotes] = useState(item?.notes ?? '')

  if (!item) return null

  return (
    <div className="sheet-overlay" onClick={onClose}>
      <div className="sheet" onClick={e => e.stopPropagation()}>
        <div className="sheet__handle" />
        <h3 className="sheet__title">Edit Item</h3>
        <label className="sheet__label">Title</label>
        <input value={title} onChange={e => setTitle(e.target.value)} />
        <label className="sheet__label">Due Date</label>
        <input type="date" value={dueDate} onChange={e => setDueDate(e.target.value)} />
        <label className="sheet__label">Notes</label>
        <textarea rows={3} value={notes} onChange={e => setNotes(e.target.value)} />
        <div className="sheet__actions">
          {onDelete && <button className="btn btn--ghost" onClick={onDelete}>Delete</button>}
          <button className="btn btn--secondary" onClick={onClose}>Cancel</button>
          <button className="btn btn--primary" onClick={() => onSave({ title, due_date: dueDate || null, notes: notes || null })}>
            Save
          </button>
        </div>
      </div>
    </div>
  )
}
