import type { ReactNode } from 'react'

// The red-title + grey-subtitle header repeated across every module page.
export function PageHeader({ title, subtitle, right }: { title: string; subtitle?: string; right?: ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: '.8rem' }}>
      <span className="font-type" style={{ color: 'var(--red)', fontWeight: 700 }}>{title}</span>
      {subtitle && <span style={{ color: 'var(--pencil)', fontSize: '.85rem' }}>{subtitle}</span>}
      {right && <div style={{ marginLeft: 'auto' }}>{right}</div>}
    </div>
  )
}

// The handwritten sticky-note empty state used by every "nothing here yet" case.
export function EmptyNote({ children, rotate = -1 }: { children: ReactNode; rotate?: number }) {
  return (
    <div className="sticky-note" style={{ transform: `rotate(${rotate}deg)`, width: 'fit-content', maxWidth: '34rem' }}>
      {children}
    </div>
  )
}

// The grey "loading…" line.
export function Loading() {
  return <p className="pencil">зарежда…</p>
}
