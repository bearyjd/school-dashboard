import type { ReactNode } from 'react'
import './design.css'

interface RowProps {
  title: string
  meta?: string
  lead?: ReactNode
  trail?: ReactNode
  href?: string
}

function safeHref(href: string | undefined): string | null {
  if (!href) return null
  try {
    const u = new URL(href)
    return u.protocol === 'http:' || u.protocol === 'https:' ? u.href : null
  } catch {
    return null
  }
}

export function Row({ title, meta, lead, trail, href }: RowProps) {
  const url = safeHref(href)
  return (
    <div className="row">
      {lead}
      <div className="row__main">
        {url ? (
          <a className="row__title" href={url} target="_blank" rel="noopener noreferrer">{title}</a>
        ) : (
          <span className="row__title">{title}</span>
        )}
        {meta && <span className="row__meta">{meta}</span>}
      </div>
      {trail}
    </div>
  )
}
