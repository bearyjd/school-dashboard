import { useEffect, useRef } from 'react'

export const TOAST_MS = 5000

interface Props {
  text: string
  onUndo?: () => void
  onDismiss: () => void
}

/** A short confirmation that goes away after 5 s, with an optional Undo. */
export function Toast({ text, onUndo, onDismiss }: Props) {
  const dismiss = useRef(onDismiss)
  useEffect(() => { dismiss.current = onDismiss })
  useEffect(() => {
    const timer = setTimeout(() => dismiss.current(), TOAST_MS)
    return () => clearTimeout(timer)
  }, [text])

  return (
    <div className="recon-toast" role="status" aria-live="polite">
      <span>{text}</span>
      {onUndo && (
        <button type="button" className="recon-btn recon-toast__undo" onClick={() => { onUndo(); onDismiss() }}>Undo</button>
      )}
    </div>
  )
}
