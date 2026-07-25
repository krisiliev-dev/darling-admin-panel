import { useState, useEffect } from 'react'
import { useApi } from '../lib/useApi'
import type { ProductsResponse } from '../lib/api'
import { Loading } from './PageHeader'

const eur = (n: number) => n.toLocaleString('bg-BG', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' €'
const COLS: { key: string; label: string; num?: boolean }[] = [
  { key: 'name', label: 'Артикул' }, { key: 'code', label: 'Код' },
  { key: 'supplier', label: 'Доставчик' },
  { key: 'units', label: 'Продадени', num: true }, { key: 'rev', label: 'Приход', num: true },
  { key: 'marg', label: 'Марж %', num: true }, { key: 'stock', label: 'Налич.', num: true },
  { key: 'price', label: 'Цена', num: true }, { key: 'stockval', label: 'Стойност', num: true },
  { key: 'cover', label: 'Дни', num: true }, { key: 'last', label: 'Посл. прод.' },
]
const STATUSES: [string, string][] = [['all', 'всички'], ['dead', 'застояли'], ['active', 'активни'], ['discontinued', 'спрени']]
const PER = 50

// Interactive explorer over the (historical) product catalogue — server-side filter (supplier /
// status / name), column sort, and pagination. Mirrors the reviewer /analytics product table.
export function ProductExplorer({ suppliers, focusSupplier }: { suppliers: string[]; focusSupplier?: string }) {
  const [supplier, setSupplier] = useState('')
  const [status, setStatus] = useState('all')
  const [q, setQ] = useState(''); const [submitted, setSubmitted] = useState('')
  const [sort, setSort] = useState('stockval'); const [order, setOrder] = useState('desc')
  const [page, setPage] = useState(1)

  // drill-down from the suppliers table: when the parent sets a supplier, adopt it as the filter
  useEffect(() => {
    if (focusSupplier) { setSupplier(focusSupplier); setPage(1) }
  }, [focusSupplier])

  const path = `/api/inventory-health/products?supplier=${encodeURIComponent(supplier)}&status=${status}`
    + `&q=${encodeURIComponent(submitted)}&sort=${sort}&order=${order}&page=${page}&per_page=${PER}`
  const { data, loading } = useApi<ProductsResponse>(path, [supplier, status, submitted, sort, order, page])

  const clickSort = (key: string) => {
    if (sort === key) setOrder((o) => (o === 'asc' ? 'desc' : 'asc'))
    else { setSort(key); setOrder('desc') }
    setPage(1)
  }
  const onFilter = <T,>(setter: (v: T) => void) => (v: T) => { setter(v); setPage(1) }

  const total = data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / PER))
  const th = { fontSize: '.62rem', textTransform: 'uppercase' as const, letterSpacing: '.04em', color: 'var(--red)', cursor: 'pointer', whiteSpace: 'nowrap' as const, padding: '.2rem .35rem', userSelect: 'none' as const }

  return (
    <div style={{ display: 'grid', gap: '.6rem' }}>
      {/* filters */}
      <div className="no-print" style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
        <select className="input-paper" aria-label="доставчик" value={supplier} onChange={(e) => onFilter(setSupplier)(e.target.value)} style={{ maxWidth: '14rem' }}>
          <option value="">— всички доставчици —</option>
          {suppliers.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <select className="input-paper" aria-label="статус" value={status} onChange={(e) => onFilter(setStatus)(e.target.value)}>
          {STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <input className="input-paper" style={{ width: '12rem' }} placeholder="търси по име или код…" value={q}
          onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') { setSubmitted(q); setPage(1) } }} aria-label="търсене" />
        <button className="btn-paper" onClick={() => { setSubmitted(q); setPage(1) }}>Търси</button>
        <button className="btn-paper" onClick={() => window.print()} title="печат на текущата страница">🖨 Печат</button>
        <span style={{ marginLeft: 'auto', fontSize: '.76rem', color: 'var(--pencil)' }}>{total.toLocaleString('bg-BG')} артикула</span>
      </div>

      {loading && !data ? <Loading /> : (
        <div style={{ overflowX: 'auto' }}>
          <table className="font-type" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.78rem' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--line)' }}>
                {COLS.map((c) => (
                  <th key={c.key} onClick={() => clickSort(c.key)} style={{ ...th, textAlign: c.num ? 'right' : 'left' }} title="сортирай">
                    {c.label}{sort === c.key ? (order === 'asc' ? ' ▲' : ' ▼') : ''}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(data?.products || []).map((p, idx) => (
                <tr key={idx} style={{ borderBottom: '1px dashed var(--line)' }}>
                  <td style={{ padding: '.25rem .35rem', maxWidth: '18rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={p.name}>
                    {p.dead && <span title="застояла" style={{ color: 'var(--blocked)' }}>● </span>}
                    {p.discontinued && <span title="спрян" style={{ color: 'var(--pencil)' }}>✕ </span>}
                    {p.name}
                  </td>
                  <td style={{ padding: '.25rem .35rem', color: 'var(--pencil)', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>{p.code || '—'}</td>
                  <td style={{ padding: '.25rem .35rem', color: 'var(--pencil)', maxWidth: '10rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={p.supplier}>{p.supplier}</td>
                  <td style={{ padding: '.25rem .35rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{p.units}</td>
                  <td style={{ padding: '.25rem .35rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{eur(p.rev)}</td>
                  <td style={{ padding: '.25rem .35rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{p.marg}%</td>
                  <td style={{ padding: '.25rem .35rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{p.stock}</td>
                  <td style={{ padding: '.25rem .35rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{p.price != null ? eur(p.price) : '—'}</td>
                  <td style={{ padding: '.25rem .35rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: p.dead ? 'var(--blocked)' : undefined }}>{eur(p.stockval)}</td>
                  <td style={{ padding: '.25rem .35rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{p.cover < 0 ? '—' : p.cover}</td>
                  <td style={{ padding: '.25rem .35rem', color: 'var(--pencil)' }}>{p.last || '—'}</td>
                </tr>
              ))}
              {data && data.products.length === 0 && (
                <tr><td colSpan={COLS.length} style={{ padding: '.6rem', color: 'var(--pencil)' }}>Няма артикули за този филтър.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* pager */}
      {pages > 1 && (
        <div style={{ display: 'flex', gap: '.6rem', alignItems: 'center', justifyContent: 'flex-end', fontSize: '.78rem' }}>
          <button className="btn-paper" disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>←</button>
          <span className="font-type" style={{ color: 'var(--pencil)' }}>стр. {page} / {pages}</span>
          <button className="btn-paper" disabled={page >= pages} onClick={() => setPage((p) => Math.min(pages, p + 1))}>→</button>
        </div>
      )}
    </div>
  )
}
