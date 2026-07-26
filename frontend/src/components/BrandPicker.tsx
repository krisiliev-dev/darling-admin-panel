// Brand picker: autocompletes over the gazetteer (+ panel-added brands) so staff pick the canonical
// spelling; if the typed brand isn't known, offers to add it (panel store, source=panel). Shared by
// the single-product editor (Intake) and delivery intake (IntakeDelivery) so both write a canonical
// "Марка" onto the Barsy article.
import { useEffect, useState } from 'react'

import { api } from '../lib/api'

export function BrandPicker({ value, onPick }: { value: string; onPick: (b: string) => void }) {
  const [q, setQ] = useState(value)
  const [open, setOpen] = useState(false)
  const [opts, setOpts] = useState<{ name: string }[]>([])
  const [known, setKnown] = useState(true)
  const [adding, setAdding] = useState(false)
  useEffect(() => { setQ(value) }, [value])
  useEffect(() => {
    if (!open) return
    const t = setTimeout(() => {
      api.get<{ brands: { name: string }[]; known: boolean }>(`/api/brands?q=${encodeURIComponent(q)}&limit=20`)
        .then((r) => { setOpts(r.brands); setKnown(r.known || !q.trim()) }).catch(() => setOpts([]))
    }, 200)
    return () => clearTimeout(t)
  }, [q, open])
  const add = async () => {
    const name = q.trim()
    if (!name) return
    setAdding(true)
    try { await api.post('/api/brands', { name }); onPick(name); setOpen(false); setKnown(true) }
    catch { /* ignore */ } finally { setAdding(false) }
  }
  return (
    <div style={{ position: 'relative' }}>
      <input className="input-paper" placeholder="търси или въведи бранд…" value={q}
        onFocus={() => setOpen(true)} onChange={(e) => { setQ(e.target.value); setOpen(true) }}
        onBlur={() => setTimeout(() => setOpen(false), 180)} />
      {open && (
        <div style={{ position: 'absolute', zIndex: 10, background: 'var(--paper)', border: '1px solid var(--line)', borderRadius: 3, maxHeight: '13rem', overflowY: 'auto', width: '100%', boxShadow: '2px 3px 7px rgba(0,0,0,.14)' }}>
          {opts.map((o) => (
            <button key={o.name} onMouseDown={() => { onPick(o.name); setQ(o.name); setOpen(false) }}
              style={{ display: 'block', width: '100%', textAlign: 'left', padding: '.35rem .5rem', border: 'none', borderBottom: '1px solid var(--line)', background: 'transparent', cursor: 'pointer', fontSize: '.8rem' }}>
              {o.name}
            </button>
          ))}
          {q.trim() && !known && !opts.some((o) => o.name.toLowerCase() === q.trim().toLowerCase()) && (
            <button onMouseDown={(e) => { e.preventDefault(); void add() }} disabled={adding}
              style={{ display: 'block', width: '100%', textAlign: 'left', padding: '.4rem .5rem', border: 'none', background: 'var(--paper2)', cursor: 'pointer', fontSize: '.78rem', color: 'var(--red)' }}>
              {adding ? 'добавя…' : `+ Добави нов бранд „${q.trim()}“`}
            </button>
          )}
        </div>
      )}
    </div>
  )
}
