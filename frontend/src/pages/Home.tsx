import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useApi } from '../lib/useApi'
import type { HomeResponse, AgingResponse, IssuesResp } from '../lib/api'
import { Sheet } from '../components/Paper'
import { Loading } from '../components/PageHeader'
import { Stat } from '../components/Stat'

const PERIODS: [string, string][] = [['today', 'Днес'], ['7d', '7 дни'], ['30d', '30 дни']]
const eur = (n: number) => n.toLocaleString('bg-BG', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' €'

// Начало — the operator cockpit + landing. Quick-glance KPIs for a period, an attention strip,
// and the orders still needing packing. Live numbers fill in as the store trades; pre-launch it's
// a calm set of zeros with clear empty states.
export function Home() {
  const [period, setPeriod] = useState('today')
  const { data, error } = useApi<HomeResponse>(`/api/summary/home?period=${period}`, [period])
  const { data: aging } = useApi<AgingResponse>('/api/inventory-health/aging')
  const { data: issues } = useApi<IssuesResp>('/api/issues?resolved=false')

  if (error) return <Sheet><p style={{ color: 'var(--red)' }}>Грешка при зареждане: {error}</p></Sheet>
  if (!data) return <Sheet><Loading /></Sheet>

  const k = data.kpi
  const stale = aging?.stale?.value ?? null
  const grid = { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(9.5rem, 1fr))', gap: '.8rem' } as const
  const tileLink = { textDecoration: 'none', color: 'inherit', display: 'block' } as const

  // combine server attention with the stale-stock signal + any open sync issues (loudest first)
  const openIssues = issues?.open_count ?? 0
  const attention = [
    ...(openIssues > 0 ? [{ tone: 'blocked', text: `${openIssues} нерешен${openIssues === 1 ? '' : 'и'} проблем${openIssues === 1 ? '' : 'а'} със синхронизацията Barsy ⇄ Shopify`, to: '/issues' }] : []),
    ...data.attention,
    ...(stale && stale > 0 ? [{ tone: 'blocked', text: `${eur(stale)} замразени в застояла стока`, to: '/inventory' }] : []),
  ]

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      {/* header + period toggle */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '.8rem', flexWrap: 'wrap' }}>
        <span className="font-type" style={{ color: 'var(--red)', fontWeight: 700, fontSize: '1.05rem' }}>Начало</span>
        <div style={{ display: 'flex', gap: '.3rem', marginLeft: 'auto' }}>
          {PERIODS.map(([k2, label]) => (
            <button key={k2} onClick={() => setPeriod(k2)} className="font-type"
              style={{
                padding: '.3rem .8rem', fontSize: '.76rem', cursor: 'pointer', borderRadius: 6,
                border: '1px solid var(--line)',
                background: period === k2 ? 'var(--red)' : 'var(--paper2)',
                color: period === k2 ? '#fff' : 'var(--pencil)',
              }}>{label}</button>
          ))}
        </div>
      </div>

      {/* Внимание strip */}
      {attention.length > 0 && (
        <div style={{ display: 'grid', gap: '.35rem' }}>
          {attention.map((a, i) => (
            <Link key={i} to={a.to} style={{ textDecoration: 'none' }}>
              <div style={{
                display: 'flex', alignItems: 'center', gap: '.5rem', fontSize: '.85rem',
                background: 'var(--paper2)', border: '1px solid var(--line)',
                borderLeft: `4px solid var(--${a.tone === 'blocked' ? 'blocked' : 'pending'})`,
                borderRadius: 4, padding: '.5rem .8rem', color: 'var(--ink)',
              }}>
                <span aria-hidden>⚠</span><span>{a.text}</span>
                <span style={{ marginLeft: 'auto', color: 'var(--red)', fontSize: '.78rem' }} className="font-type">виж →</span>
              </div>
            </Link>
          ))}
        </div>
      )}

      {/* KPI cards */}
      <section style={grid}>
        <Stat label="Оборот" value={k.turnover} money instant hint="продажби за периода" />
        <Stat label="Приход" value={k.profit} money instant
          hint={k.profit_complete ? 'оборот − себестойност' : '≈ приблизително (липсва себестойност за някои)'} />
        {/* orders: two numbers on one chit */}
        <div style={{ background: 'var(--paper2)', border: '1px solid var(--line)', borderRadius: 2, padding: '.85rem 1rem', boxShadow: '2px 3px 7px rgba(0,0,0,.13)' }}>
          <div className="font-type" style={{ fontSize: '.62rem', letterSpacing: '.1em', textTransform: 'uppercase', color: 'var(--red)', marginBottom: '.25rem' }}>Поръчки</div>
          <div className="font-type" style={{ display: 'flex', gap: '.9rem', alignItems: 'baseline' }}>
            <span style={{ fontSize: '1.7rem', fontWeight: 700, color: k.orders_to_pack ? 'var(--pending)' : 'var(--ink)', lineHeight: 1 }}>{k.orders_to_pack}</span>
            <span style={{ fontSize: '1.1rem', fontWeight: 700, color: 'var(--pencil)' }}>/ {k.orders_done}</span>
          </div>
          <div style={{ fontSize: '.7rem', color: 'var(--pencil)', marginTop: '.2rem' }}>за пакетиране · завършени</div>
        </div>
        <Stat label="Продадени артикули" value={k.units} instant hint="бройки за периода" />
        {/* reorder items are acted on in Заявки (поръчки към доставчици), not the receiving screen */}
        <Link to="/zayavki" style={tileLink}>
          <Stat label="За дозареждане" value={k.reorder_n} instant tone={k.reorder_n > 0 ? 'pending' : undefined} hint="под минимума" />
        </Link>
        {/* dead-stock detail lives in Анализи → Склад, not the product catalogue */}
        <Link to="/analytics" style={tileLink}>
          <Stat label="Застояла стока" value={stale ?? '—'} money={stale != null} instant tone={stale && stale > 0 ? 'blocked' : undefined} hint="замразен капитал" />
        </Link>
        <Stat label="Най-продаван" value={k.top_product ? k.top_product.name.slice(0, 22) : '—'} instant hint={k.top_product ? `${k.top_product.units} бр за периода` : 'няма продажби още'} />
      </section>

      {/* orders that need packing + sending */}
      <section>
        <div className="eyebrow" style={{ marginBottom: '.4rem' }}>За опаковане и изпращане</div>
        {data.to_pack.length === 0 ? (
          <div className="sticky-note" style={{ transform: 'rotate(-.7deg)', width: 'fit-content', maxWidth: '34rem' }}>
            Няма поръчки за пакетиране. Щом дойдат от Shopify, тук ще излизат картончета с продуктите,
            клиента и връзка към етикета.
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(17rem, 1fr))', gap: '.8rem' }}>
            {data.to_pack.map((o) => (
              <div key={o.id} style={{ background: 'var(--paper2)', border: '1px solid var(--line)', borderRadius: 4, padding: '.8rem .9rem', boxShadow: '2px 3px 7px rgba(0,0,0,.12)', display: 'grid', gap: '.5rem' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <span className="font-type" style={{ fontWeight: 700 }}>{o.name}</span>
                  <span className="font-type" style={{ fontVariantNumeric: 'tabular-nums' }}>{eur(o.total)}</span>
                </div>
                <div style={{ fontSize: '.78rem', color: 'var(--pencil)' }}>
                  {o.customer || '—'}{o.city && ` · ${o.city}`}{o.op_status_bg && ` · ${o.op_status_bg}`}
                </div>
                <div style={{ display: 'grid', gap: '.25rem', maxHeight: '7.5rem', overflowY: 'auto' }}>
                  {o.items.map((it, i) => (
                    <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '.5rem', fontSize: '.76rem' }}>
                      <span aria-hidden style={{ flex: 'none', width: 28, height: 28, borderRadius: 3, background: 'var(--sunk, var(--line))', display: 'grid', placeItems: 'center', color: 'var(--pencil)', fontSize: '.7rem' }} title={it.sku || ''}>
                        {(it.title || '?').slice(0, 1)}
                      </span>
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={it.title}>{it.title}</span>
                      <span style={{ marginLeft: 'auto', color: 'var(--pencil)' }}>×{it.qty}</span>
                    </div>
                  ))}
                </div>
                <Link to="/orders" className="font-type" style={{ fontSize: '.76rem', color: 'var(--red)', textDecoration: 'none' }}>Генерирай етикет →</Link>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
