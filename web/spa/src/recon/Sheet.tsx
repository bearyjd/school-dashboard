import { useLayoutEffect, useRef, type KeyboardEvent, type ReactNode } from 'react'

const FOCUSABLE = 'button, a[href], textarea, input, select, [tabindex]:not([tabindex="-1"])'

interface SheetProps {
  label: string
  /** The control that opened the sheet; focus goes back to it on close. */
  opener: HTMLElement | null
  onClose: () => void
  children: ReactNode
}

/** A bottom sheet (centred on wide screens): focus moves in, Tab stays inside, Escape or the backdrop closes it. */
export function Sheet({ label, opener, onClose, children }: SheetProps) {
  const ref = useRef<HTMLDivElement>(null)

  // Layout effect: focus is inside the dialog in the same commit that shows it (no moment where it is elsewhere).
  useLayoutEffect(() => {
    ref.current?.focus()
    return () => { if (opener?.isConnected) opener.focus() }
  }, [opener])

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      e.stopPropagation()
      onClose()
      return
    }
    if (e.key !== 'Tab') return
    const els = Array.from(ref.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []).filter(el => !el.hasAttribute('disabled'))
    const [first, last] = [els[0], els[els.length - 1]]
    if (!first || !last) {
      e.preventDefault()
    } else if (e.shiftKey && (document.activeElement === first || document.activeElement === ref.current)) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault()
      first.focus()
    }
  }

  return (
    <div className="recon-sheet__overlay" onClick={onClose}>
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        className="recon-sheet"
        onKeyDown={onKeyDown}
        onClick={e => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  )
}
