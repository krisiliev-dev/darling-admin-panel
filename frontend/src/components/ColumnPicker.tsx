import { useMemo, useState } from 'react'
import type { ColumnMeta, ColumnPrefs } from '../lib/useColumnPrefs'

// The „wrench" column picker, modelled on Barsy's „Настройка" → Колони: a searchable, sectioned list
// of toggles with a reset-to-default. The caller owns the columns and a useColumnPrefs(tableId,
// columns); this renders the trigger button + the popover and drives the prefs. Table-agnostic on
// purpose, so every product surface (Каталог, Ценообразуване, …) can share one picker.
export function ColumnPicker({ columns, prefs, label = 'колони' }: {
  columns: ColumnMeta[]
  prefs: ColumnPrefs
  label?: string
}) {
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')

  const groups = useMemo(() => {
    const m = new Map<string, ColumnMeta[]>()
    for (const c of columns) {
      const g = c.group ?? ''
      const arr = m.get(g) ?? []
      arr.push(c)
      m.set(g, arr)
    }
    return m
  }, [columns])

  const needle = q.trim().toLowerCase()
  const matches = (c: ColumnMeta) => !needle || c.label.toLowerCase().includes(needle)

  return (
    <div style={{ position: 'relative', display: 'inline-block' }}>
      <button className="btn-paper" style={{ fontSize: '.72rem' }} aria-haspopup="dialog"
        aria-expanded={open} title="избор на колони" onClick={() => setOpen((o) => !o)}>
        🔧 {label}
      </button>
      {open && (
        <>
          <div onClick={() => setOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 40 }} />
          <div role="dialog" aria-label="Избор на колони"
            style={{
              position: 'absolute', right: 0, top: 'calc(100% + .3rem)', zIndex: 41, width: '15rem',
              background: 'var(--paper)', border: '1px solid var(--red)', borderRadius: 4,
              boxShadow: '0 8px 24px rgba(0,0,0,.25)', padding: '.6rem', display: 'grid', gap: '.4rem',
            }}>
            <input className="input-paper" type="search" placeholder="търси колона…" value={q}
              autoFocus onChange={(e) => setQ(e.target.value)} />
            <div style={{ maxHeight: '17rem', overflow: 'auto', display: 'grid', gap: '.25rem' }}>
              {[...groups.entries()].map(([g, cols]) => {
                const vis = cols.filter(matches)
                if (vis.length === 0) return null
                return (
                  <div key={g || '_'} style={{ display: 'grid', gap: '.15rem' }}>
                    {g && (
                      <div className="font-type" style={{ fontSize: '.6rem', color: 'var(--pencil)', textTransform: 'uppercase', letterSpacing: '.05em', marginTop: '.2rem' }}>{g}</div>
                    )}
                    {vis.map((c) => (
                      <label key={c.key} style={{ display: 'flex', gap: '.4rem', alignItems: 'center', fontSize: '.8rem', cursor: c.fixed ? 'default' : 'pointer', opacity: c.fixed ? 0.55 : 1 }}>
                        <input type="checkbox" checked={prefs.isVisible(c.key)} disabled={c.fixed}
                          onChange={() => prefs.toggle(c.key)} />
                        <span>{c.label}</span>
                      </label>
                    ))}
                  </div>
                )
              })}
            </div>
            <button className="btn-paper" style={{ fontSize: '.7rem' }} onClick={() => prefs.reset()}>по подразбиране</button>
          </div>
        </>
      )}
    </div>
  )
}
