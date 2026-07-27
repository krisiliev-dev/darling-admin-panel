import { useEffect } from 'react'
import { useApi } from '../lib/useApi'
import { PageHeader, Loading, EmptyNote } from '../components/PageHeader'
import { Sheet } from '../components/Paper'
import type { CartsResp, CartItem } from '../lib/api'

function CartRow({ c }: { c: CartItem }) {
  const who = c.email || c.phone || 'анонимен'
  return (
    <div className="font-type" style={{ display: 'flex', justifyContent: 'space-between', gap: '.5rem', fontSize: '.8rem', borderBottom: '1px dashed var(--line)', padding: '.3rem 0' }}>
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {c.item_count} артикул{c.item_count === 1 ? '' : 'а'} · {who}{c.name ? ` · ${c.name}` : ''}
      </span>
      <span style={{ fontWeight: 700, whiteSpace: 'nowrap' }}>{c.total_price ? `${c.total_price} ${c.currency}` : '—'}</span>
    </div>
  )
}

function Stat({ n, label, tone }: { n: number; label: string; tone: 'ready' | 'red' }) {
  return (
    <Sheet clip="right">
      <div style={{ fontSize: '2rem', fontWeight: 800, lineHeight: 1, color: `var(--${tone})` }}>{n}</div>
      <div className="font-type" style={{ fontSize: '.78rem', color: 'var(--pencil)', marginTop: '.2rem' }}>{label}</div>
    </Sheet>
  )
}

// Кошници — live + abandoned Shopify checkouts, fed by the checkouts webhook (read-only, never
// touches Barsy). Auto-refreshes so the owner can watch carts fill in real time.
export function Carts() {
  const { data, loading, reload } = useApi<CartsResp>('/api/carts')
  useEffect(() => { const t = setInterval(reload, 20000); return () => clearInterval(t) }, [reload])
  const d = data
  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <PageHeader title="Кошници" subtitle="активни и изоставени колички от онлайн магазина" />
      {loading && !d ? <Loading /> : (
        <>
          <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
            <Stat n={d?.active_count ?? 0} label="активни в момента" tone="ready" />
            <Stat n={d?.abandoned_count ?? 0} label="изоставени (>30 мин)" tone="red" />
          </div>
          <Sheet>
            <h3 style={{ fontSize: '.95rem', marginBottom: '.4rem' }}>🛒 Активни ({d?.active_count ?? 0})</h3>
            {!d || d.active.length === 0
              ? <EmptyNote>Никой няма нещо в количката в момента.</EmptyNote>
              : d.active.map((c) => <CartRow key={c.token} c={c} />)}
          </Sheet>
          {d && d.abandoned.length > 0 && (
            <Sheet>
              <h3 style={{ fontSize: '.95rem', marginBottom: '.4rem' }}>🕳 Изоставени ({d.abandoned_count})</h3>
              {d.abandoned.map((c) => <CartRow key={c.token} c={c} />)}
            </Sheet>
          )}
          <p className="pencil" style={{ fontSize: '.75rem', margin: 0 }}>
            Обновява се автоматично от Shopify (checkouts webhook). Само за онлайн магазина — не влиза в Barsy.
          </p>
        </>
      )}
    </div>
  )
}
