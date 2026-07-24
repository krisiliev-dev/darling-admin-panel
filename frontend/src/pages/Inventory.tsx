import { useState } from 'react'
import { useApi } from '../lib/useApi'
import type { InventoryResponse } from '../lib/api'
import { Sheet } from '../components/Paper'
import { Stat } from '../components/Stat'
import { Pager, usePager } from '../components/TableControls'
import { PageHeader, EmptyNote, Loading } from '../components/PageHeader'

// Наличности — live stock from Barsy with low-stock detection. Barsy fills as products
// clear intake approval, so today this is near-empty; it grows with the catalog.
export function Inventory() {
  const [q, setQ] = useState('')
  const [submitted, setSubmitted] = useState('')  // q applied on Търси / Enter
  const [lowOnly, setLowOnly] = useState(false)

  const path = `/api/inventory?low_only=${lowOnly}&q=${encodeURIComponent(submitted)}`
  const { data, error, loading, reload } = useApi<InventoryResponse>(path, [lowOnly, submitted])

  const search = () => { if (submitted === q) reload(); else setSubmitted(q) }
  const pg = usePager(data?.items || [], 50)  // a broad stock search can be large — page it

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <PageHeader title="Наличности" subtitle="жив запас от Barsy" />

      {data && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(9rem, 1fr))', gap: '.8rem' }}>
          <Stat label="Артикули" value={data.summary.tracked} hint="в Barsy" />
          <Stat label="Под минимум" value={data.summary.low} tone="blocked" hint="за поръчка" />
          <Stat label="Нулева наличност" value={data.summary.zero} tone="pending" />
        </div>
      )}

      <Sheet clip="right">
        <div style={{ display: 'flex', gap: '.5rem', alignItems: 'center', flexWrap: 'wrap', marginBottom: '.7rem' }}>
          <input className="input-paper" style={{ width: '14rem' }} placeholder="търсене по име"
            aria-label="търсене на артикул по име"
            value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') search() }} />
          <button className="btn-paper" onClick={search}>Търси</button>
          <label className="font-type" style={{ fontSize: '.78rem', display: 'flex', gap: '.3rem', alignItems: 'center', marginLeft: '.4rem' }}>
            <input type="checkbox" checked={lowOnly} onChange={(e) => setLowOnly(e.target.checked)} />
            само под минимум
          </label>
        </div>

        {error && <p style={{ color: 'var(--red)', fontFamily: 'var(--type)', fontSize: '.82rem' }}>Грешка: {error}</p>}
        {loading && !data && <Loading />}
        {data && data.items.length === 0 && (
          <EmptyNote>
            Няма артикули за показване. Наличностите идват от Barsy — пълнят се, щом продуктите
            минат през „Приемане“ и се създадат в Barsy. Ниският запас ще се маркира тук в червено.
          </EmptyNote>
        )}
        {data && data.items.length > 0 && (
          <div style={{ display: 'grid', gap: '.3rem' }}>
            <div className="font-type" style={{ display: 'grid', gridTemplateColumns: '1fr 6rem 7rem 7rem', gap: '.5rem', fontSize: '.62rem', textTransform: 'uppercase', color: 'var(--red)', letterSpacing: '.08em', paddingBottom: '.2rem', borderBottom: '1px solid var(--line)' }}>
              <span>Артикул</span><span style={{ textAlign: 'right' }}>Наличност</span><span style={{ textAlign: 'right' }}>Минимум</span><span style={{ textAlign: 'right' }}>Ср. цена</span>
            </div>
            {pg.slice.map((it) => (
              <div key={it.article_id} style={{
                display: 'grid', gridTemplateColumns: '1fr 6rem 7rem 7rem', gap: '.5rem', alignItems: 'center',
                fontSize: '.82rem', padding: '.35rem 0', borderBottom: '1px dashed var(--line)',
                borderLeft: it.low ? '3px solid var(--blocked)' : '3px solid transparent', paddingLeft: '.5rem',
              }}>
                <span>
                  {it.name}
                  {/* status by word+color, not color alone (WCAG 1.4.1) */}
                  {it.zero ? <span className="font-type" style={{ color: 'var(--blocked)', fontSize: '.62rem', marginLeft: '.4rem' }}>● нулев</span>
                    : it.low ? <span className="font-type" style={{ color: 'var(--pending)', fontSize: '.62rem', marginLeft: '.4rem' }}>● нисък</span> : null}
                </span>
                <span className="font-type" style={{ textAlign: 'right', color: it.zero ? 'var(--blocked)' : it.low ? 'var(--pending)' : 'var(--ink)', fontWeight: 700 }}>
                  {it.amount ?? '—'} {it.unit}
                </span>
                <span className="font-type" style={{ textAlign: 'right', color: 'var(--pencil)' }}>{it.min_amount ?? '—'}</span>
                <span className="font-type" style={{ textAlign: 'right', color: 'var(--pencil)' }}>{it.avg_cost != null ? it.avg_cost.toFixed(2) + ' €' : '—'}</span>
              </div>
            ))}
            <Pager {...pg} />
          </div>
        )}
      </Sheet>
    </div>
  )
}
