import { useState } from 'react'

// Top-of-table filter chips + client-side pagination. Data-table UX: filters live above the table,
// pagination conveys volume and keeps bulk actions predictable. Purely presentational + a tiny hook.

export interface ChipOption<T extends string> { key: T; label: string; count?: number }

export function FilterChips<T extends string>({ options, value, onChange }: {
  options: ChipOption<T>[]; value: T; onChange: (k: T) => void
}) {
  return (
    <div style={{ display: 'flex', gap: '.3rem', flexWrap: 'wrap' }} role="tablist" aria-label="филтри">
      {options.map((o) => {
        const active = o.key === value
        return (
          <button key={o.key} role="tab" aria-selected={active} onClick={() => onChange(o.key)}
            className="font-type"
            style={{
              padding: '.22rem .6rem', fontSize: '.7rem', cursor: 'pointer', borderRadius: 2,
              border: '1px solid var(--line)',
              background: active ? 'var(--red)' : 'var(--paper2)',
              color: active ? '#fff' : 'var(--pencil)',
            }}>
            {o.label}{o.count != null ? ` · ${o.count}` : ''}
          </button>
        )
      })}
    </div>
  )
}

// Client-side pager over an already-fetched list. Clamps the page if the list shrinks under it.
export function usePager<T>(items: T[], pageSize = 25) {
  const [page, setPage] = useState(0)
  const pages = Math.max(1, Math.ceil(items.length / pageSize))
  const clamped = Math.min(page, pages - 1)
  const start = clamped * pageSize
  return {
    slice: items.slice(start, start + pageSize),
    page: clamped, pages, setPage, total: items.length,
    from: items.length ? start + 1 : 0, to: Math.min(start + pageSize, items.length),
  }
}

export function Pager({ page, pages, setPage, from, to, total }: {
  page: number; pages: number; setPage: (n: number) => void; from: number; to: number; total: number
}) {
  if (pages <= 1) return null
  return (
    <div style={{ display: 'flex', gap: '.6rem', alignItems: 'center', justifyContent: 'flex-end', marginTop: '.5rem' }}>
      <span className="font-type" style={{ fontSize: '.72rem', color: 'var(--pencil)' }}>{from}–{to} от {total}</span>
      <button className="btn-paper" style={{ fontSize: '.72rem' }} disabled={page <= 0}
        onClick={() => setPage(page - 1)} aria-label="предишна страница">←</button>
      <span className="font-type" style={{ fontSize: '.72rem' }}>{page + 1}/{pages}</span>
      <button className="btn-paper" style={{ fontSize: '.72rem' }} disabled={page >= pages - 1}
        onClick={() => setPage(page + 1)} aria-label="следваща страница">→</button>
    </div>
  )
}
