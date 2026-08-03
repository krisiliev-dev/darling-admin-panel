import type { ReactNode } from 'react'

// Eyebrow heading over a block ("Атрибути", "Доставчици"…). Replaces the 8+ inline style objects
// that each re-declared the same .6rem uppercase letterSpacing label.
export function Section({
  title,
  tone = 'red',
  right,
  children,
}: {
  title: string
  tone?: 'red' | 'pencil'
  right?: ReactNode
  children: ReactNode
}) {
  return (
    <section style={{ display: 'grid', gap: '.4rem' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '.6rem' }}>
        <h3 className={`field-label${tone === 'pencil' ? ' field-label--pencil' : ''}`} style={{ margin: 0 }}>
          {title}
        </h3>
        {right}
      </div>
      {children}
    </section>
  )
}
