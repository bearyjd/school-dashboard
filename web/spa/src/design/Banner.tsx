import type { ReactNode } from 'react'
import './design.css'

export type BannerTone = 'info' | 'warn' | 'error'

const ICONS: Record<BannerTone, string> = { info: 'i', warn: '⚠', error: '✖' }

export function Banner({ tone, children }: { tone: BannerTone; children: ReactNode }) {
  return (
    <div className={`banner banner--${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      <span aria-hidden="true">{ICONS[tone]}</span>
      <span>{children}</span>
    </div>
  )
}
