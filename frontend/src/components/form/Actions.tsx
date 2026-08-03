import type { ReactNode } from 'react'

// The save/cancel row plus the one-line status message that every editor re-rolled by hand.
// `error` and `note` are separate props on purpose: an error must never be silently rendered in
// the "ok" tone because a page passed the wrong string.
export function Msg({ tone, children }: { tone: 'error' | 'ok' | 'pending'; children: ReactNode }) {
  return (
    <span className={`msg msg--${tone}`} role={tone === 'error' ? 'alert' : undefined}>
      {children}
    </span>
  )
}

export function ActionBar({
  busy,
  error,
  note,
  children,
}: {
  busy?: boolean
  error?: string
  note?: string
  children: ReactNode
}) {
  return (
    <div
      style={{
        display: 'flex',
        gap: '.6rem',
        flexWrap: 'wrap',
        alignItems: 'center',
        marginTop: '.6rem',
      }}
    >
      {children}
      {error ? <Msg tone="error">{error}</Msg> : note ? <Msg tone={busy ? 'pending' : 'ok'}>{note}</Msg> : null}
    </div>
  )
}
