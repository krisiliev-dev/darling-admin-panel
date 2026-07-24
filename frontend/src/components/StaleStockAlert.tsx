import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useApi } from '../lib/useApi'
import type { AgingResponse } from '../lib/api'

const eur = (n: number) =>
  n.toLocaleString('bg-BG', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' €'

// calm → alarm, matched to the ageing buckets (fresh stock is fine; old stock is frozen capital)
const BUCKET_TONE: Record<string, string> = {
  '0-90': 'var(--ready, #0a8a4a)',
  '90-180': 'var(--pencil, #8b897f)',
  '180-365': 'var(--pending, #c77a05)',
  '365+': 'var(--blocked, #d33f3f)',
}

/**
 * Застоял инвентар — the manager alert the old-ERP /analytics page inspired: it surfaces stock
 * that has sat in the warehouse past a threshold (default 180 days) and the capital frozen in it,
 * then hands off into the repricing flow to prompt a deeper discount. Renders nothing when there's
 * no stale stock (or no lot data yet), so a healthy warehouse keeps the cockpit calm.
 */
export function StaleStockAlert() {
  const { data, error } = useApi<AgingResponse>('/api/inventory-health/aging')
  const [open, setOpen] = useState(false)

  if (error || !data) return null
  const { stale, buckets, totals } = data
  if (stale.articles === 0 || stale.value === 0) return null

  const barTotal = buckets.reduce((s, b) => s + b.value, 0) || 1
  const top = open ? stale.items : stale.items.slice(0, 5)

  return (
    <section>
      <div className="eyebrow" style={{ marginBottom: '.4rem' }}>Застоял инвентар</div>
      <div style={{
        background: 'var(--paper2, #fcfcfb)', border: '1px solid var(--pending, #c77a05)',
        borderLeft: '4px solid var(--pending, #c77a05)', borderRadius: 3,
        padding: '1rem 1.1rem', boxShadow: '2px 3px 7px rgba(0,0,0,.10)',
      }}>
        {/* headline: the money that's frozen */}
        <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'baseline', gap: '.5rem 1rem' }}>
          <span className="font-type" style={{ fontSize: '1.55rem', fontWeight: 700, color: 'var(--blocked, #d33f3f)' }}>
            {eur(stale.value)}
          </span>
          <span style={{ color: 'var(--ink)', fontSize: '.9rem' }}>
            замразени в <strong>{stale.articles}</strong> артикула, непродавани{' '}
            {stale.threshold_days}+ дни
          </span>
          <span style={{ color: 'var(--pencil)', fontSize: '.8rem', marginLeft: 'auto' }}>
            {stale.pct_of_value}% от стойността на склада
          </span>
        </div>

        {/* the nudge */}
        <div style={{ color: 'var(--pencil)', fontSize: '.82rem', margin: '.5rem 0 .8rem' }}>
          Стоят и заемат място. Обмислете по-голяма отстъпка, за да се продадат и освободят склада.
        </div>

        {/* ageing-by-value stacked bar */}
        <div style={{ display: 'flex', height: 12, borderRadius: 6, overflow: 'hidden', marginBottom: '.35rem' }}>
          {buckets.map((b) => b.value > 0 && (
            <div key={b.key} title={`${b.label}: ${eur(b.value)} · ${b.lots} партиди`}
              style={{ width: `${(b.value / barTotal) * 100}%`, background: BUCKET_TONE[b.key] || 'var(--pencil)' }} />
          ))}
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '.15rem 1rem', fontSize: '.7rem', color: 'var(--pencil)', marginBottom: '.9rem' }}>
          {buckets.map((b) => (
            <span key={b.key} style={{ display: 'inline-flex', alignItems: 'center', gap: '.3rem' }}>
              <span style={{ width: 8, height: 8, borderRadius: 2, background: BUCKET_TONE[b.key] || 'var(--pencil)' }} />
              {b.label} · {eur(b.value)}
            </span>
          ))}
        </div>

        {/* worst offenders by frozen capital */}
        <div style={{ display: 'grid', gap: '.3rem' }}>
          {top.map((it) => (
            <div key={it.article_id} style={{
              display: 'grid', gridTemplateColumns: '1fr auto auto auto', gap: '.6rem',
              alignItems: 'center', fontSize: '.8rem', padding: '.35rem 0',
              borderTop: '1px dashed var(--line)',
            }}>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={it.name}>
                {it.name || `#${it.article_id}`}
                {it.supplier && <span style={{ color: 'var(--pencil)' }}> · {it.supplier}</span>}
              </span>
              <span style={{ color: 'var(--blocked, #d33f3f)', fontVariantNumeric: 'tabular-nums' }}>
                {it.oldest_days} дни
              </span>
              <span style={{ fontVariantNumeric: 'tabular-nums' }} title="замразена стойност">{eur(it.value)}</span>
              <Link to={`/pricing?q=${encodeURIComponent(it.name || String(it.article_id))}&md=${it.suggested_markdown_pct}`} className="font-type"
                title={it.suggested_price ? `предложена цена ${eur(it.suggested_price)}` : 'предложи отстъпка'}
                style={{ fontSize: '.72rem', color: 'var(--red)', textDecoration: 'none', whiteSpace: 'nowrap' }}>
                {it.suggested_markdown_pct > 0 ? `−${it.suggested_markdown_pct}%` : 'Отстъпка'} →
              </Link>
            </div>
          ))}
        </div>

        <div style={{ display: 'flex', gap: '1rem', marginTop: '.7rem', fontSize: '.76rem' }}>
          {stale.items.length > 5 && (
            <button onClick={() => setOpen((v) => !v)} className="font-type"
              style={{ background: 'none', border: 'none', color: 'var(--red)', cursor: 'pointer', padding: 0 }}>
              {open ? 'скрий' : `виж всички ${stale.articles}`}
            </button>
          )}
          <span style={{ color: 'var(--pencil)', marginLeft: 'auto' }}>
            общо склад: {eur(totals.value)} · {totals.articles} артикула
          </span>
        </div>
      </div>
    </section>
  )
}
