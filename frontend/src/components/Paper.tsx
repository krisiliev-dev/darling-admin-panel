import type { ReactNode } from 'react'

// A paperclip — the recurring physical motif from the design spec.
export function Clip({ side = 'left' }: { side?: 'left' | 'right' }) {
  return (
    <svg
      viewBox="0 0 26 60" fill="none" aria-hidden
      style={{
        position: 'absolute', top: -14, width: 26, height: 60, zIndex: 3,
        [side]: 34,
      } as React.CSSProperties}>
      <path
        d="M8 54V14a6 6 0 0112 0v34a10 10 0 01-20 0V18"
        stroke="var(--clip)" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  )
}

// A sheet of bond paper. The base surface for every panel.
export function Sheet({
  children, tilt, clip, className = '', style,
}: {
  children: ReactNode
  tilt?: 'l' | 'r'
  clip?: 'left' | 'right'
  className?: string
  style?: React.CSSProperties
}) {
  const tiltClass = tilt === 'l' ? 'tilt-l' : tilt === 'r' ? 'tilt-r' : ''
  return (
    <div className={`sheet ${tiltClass} ${className}`} style={{ padding: '1.6rem 1.8rem', ...style }}>
      {clip && <Clip side={clip} />}
      {children}
    </div>
  )
}

export function Stamp({ children }: { children: ReactNode }) {
  return <span className="stamp">{children}</span>
}
