import { useEffect, useMemo, useState } from 'react'

import { api } from '../lib/api'
import type { CacheProduct, CacheSearchResp } from '../lib/api'
import { Sheet } from '../components/Paper'
import { PageHeader } from '../components/PageHeader'
import { printLabels } from '../lib/label'

// Канцелария — the store's back-office/print desk. First tool: batch label printing for the 58mm
// thermal printer. Search the catalogue, add products to a queue with a copy count, print them all
// (one label per copy). Handy for re-tagging a shelf after a price change, or tagging imports that
// arrive without a barcode.
interface QueueRow { p: CacheProduct; copies: number }

export function Kancelaria() {
  const [q, setQ] = useState('')
  const [results, setResults] = useState<CacheProduct[]>([])
  const [loading, setLoading] = useState(false)
  const [queue, setQueue] = useState<QueueRow[]>([])

  useEffect(() => {
    const term = q.trim()
    if (!term) { setResults([]); return }
    setLoading(true)
    const t = setTimeout(() => {
      api.get<CacheSearchResp>(`/api/catalog/search?q=${encodeURIComponent(term)}&per_page=25`)
        .then((d) => setResults(d.items || []))
        .catch(() => setResults([]))
        .finally(() => setLoading(false))
    }, 250)
    return () => clearTimeout(t)
  }, [q])

  const add = (p: CacheProduct) => setQueue((qq) => {
    const i = qq.findIndex((r) => r.p.article_id === p.article_id)
    if (i >= 0) { const n = [...qq]; n[i] = { ...n[i], copies: n[i].copies + 1 }; return n }
    return [...qq, { p, copies: 1 }]
  })
  const setCopies = (id: number, copies: number) =>
    setQueue((qq) => qq.map((r) => r.p.article_id === id ? { ...r, copies: Math.max(1, Math.min(99, copies || 1)) } : r))
  const remove = (id: number) => setQueue((qq) => qq.filter((r) => r.p.article_id !== id))

  const totalLabels = useMemo(() => queue.reduce((s, r) => s + r.copies, 0), [queue])

  const print = () => printLabels(queue.map((r) => ({
    name: r.p.name, price: r.p.price, barcode: r.p.barcode, code: r.p.code, copies: r.copies,
  })))

  return (
    <div style={{ display: 'grid', gap: '1rem' }}>
      <PageHeader title="Канцелария" subtitle="печат на етикети и офис задачи" />

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) minmax(0,1fr)', gap: '1rem' }}>
        {/* search */}
        <Sheet>
          <h3 style={{ fontSize: '1rem', marginBottom: '.4rem' }}>🏷 Печат на етикети</h3>
          <p className="font-type" style={{ fontSize: '.76rem', color: 'var(--pencil)', margin: '0 0 .5rem' }}>
            Търси продукт и го добави към опашката. Всеки етикет е 58мм с баркод и цена.
          </p>
          <input className="input-paper" style={{ width: '100%' }} placeholder="търси продукт по име или код…"
            value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
          <div style={{ marginTop: '.5rem', maxHeight: '22rem', overflowY: 'auto', display: 'grid', gap: '.15rem' }}>
            {loading && <span className="font-type" style={{ fontSize: '.76rem', color: 'var(--pencil)' }}>търси…</span>}
            {!loading && q.trim() && results.length === 0 &&
              <span className="font-type" style={{ fontSize: '.76rem', color: 'var(--pencil)' }}>няма резултати (или кешът не е синхронизиран).</span>}
            {results.map((p) => (
              <button key={p.article_id} onClick={() => add(p)} className="font-type"
                style={{ display: 'flex', justifyContent: 'space-between', gap: '.5rem', textAlign: 'left', width: '100%',
                  border: 'none', borderBottom: '1px dashed var(--line)', background: 'transparent', cursor: 'pointer',
                  padding: '.3rem .1rem', fontSize: '.78rem' }}>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {p.name || '(без име)'} <span style={{ color: 'var(--pencil)' }}>{p.code}</span>
                </span>
                <span style={{ whiteSpace: 'nowrap' }}>+ добави</span>
              </button>
            ))}
          </div>
        </Sheet>

        {/* queue */}
        <Sheet clip="right">
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '.4rem' }}>
            <h3 style={{ fontSize: '1rem' }}>Опашка за печат</h3>
            <span className="font-type" style={{ fontSize: '.74rem', color: 'var(--pencil)' }}>{totalLabels} етикета</span>
          </div>
          {queue.length === 0 && <p className="font-type" style={{ fontSize: '.78rem', color: 'var(--pencil)' }}>Добави продукти отляво.</p>}
          <div style={{ display: 'grid', gap: '.2rem', maxHeight: '20rem', overflowY: 'auto' }}>
            {queue.map((r) => (
              <div key={r.p.article_id} className="font-type" style={{ display: 'flex', alignItems: 'center', gap: '.4rem', borderBottom: '1px dashed var(--line)', padding: '.2rem 0', fontSize: '.78rem' }}>
                <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {r.p.name} <span style={{ color: 'var(--pencil)' }}>{r.p.price != null ? `${r.p.price} €` : ''}</span>
                </span>
                <input className="input-paper" type="number" min={1} max={99} value={r.copies}
                  onChange={(e) => setCopies(r.p.article_id, parseInt(e.target.value, 10))}
                  style={{ width: '3.5rem', textAlign: 'right' }} aria-label={`бройки за ${r.p.name}`} />
                <button onClick={() => remove(r.p.article_id)} style={{ border: 'none', background: 'transparent', cursor: 'pointer', color: 'var(--red)' }}>✕</button>
              </div>
            ))}
          </div>
          {queue.length > 0 && (
            <div style={{ display: 'flex', gap: '.5rem', marginTop: '.6rem' }}>
              <button className="btn-paper btn-red" onClick={print}>🖨 Печат ({totalLabels})</button>
              <button className="btn-paper" onClick={() => setQueue([])}>Изчисти</button>
            </div>
          )}
        </Sheet>
      </div>
    </div>
  )
}
