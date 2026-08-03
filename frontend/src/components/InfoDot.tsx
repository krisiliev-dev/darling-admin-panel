// A small "i" affordance: hover or click opens a panel explaining a field or a feature.
// Used for the markdown cheat sheet on Описание and for the AI enrichment explainer, which
// also shows WHAT the agents found and WHERE from — so the human can verify the sources.
import { useEffect, useRef, useState } from 'react'

export function InfoDot({ title, children, width = 340 }: {
  title?: string; children: React.ReactNode; width?: number
}) {
  const [open, setOpen] = useState(false)
  const wrap = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false)
    }
    const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onEsc)
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onEsc) }
  }, [open])
  return (
    <span ref={wrap} style={{ position: 'relative', display: 'inline-flex' }}
      onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button type="button" aria-label={title || 'информация'} aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        style={{
          width: '1.05rem', height: '1.05rem', lineHeight: 1, borderRadius: '50%', cursor: 'help',
          border: '1px solid var(--line)', background: 'var(--paper2)', color: 'var(--pencil)',
          fontFamily: 'var(--type)', fontSize: '.68rem', fontWeight: 700, padding: 0,
        }}>i</button>
      {open && (
        <span role="tooltip" style={{
          position: 'absolute', zIndex: 40, top: '1.3rem', left: 0, width,
          background: 'var(--paper)', border: '1px solid var(--line)', borderRadius: 3,
          boxShadow: '2px 4px 12px rgba(0,0,0,.18)', padding: '.6rem .7rem',
          fontFamily: 'var(--type)', fontSize: '.75rem', lineHeight: 1.45, color: 'var(--ink)',
          textAlign: 'left', maxHeight: '22rem', overflowY: 'auto', whiteSpace: 'normal',
        }}>
          {title && <strong style={{ display: 'block', marginBottom: '.3rem' }}>{title}</strong>}
          {children}
        </span>
      )}
    </span>
  )
}
