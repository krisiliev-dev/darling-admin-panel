import { useEffect, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { Link } from 'react-router-dom'
import { Bar, Line } from 'react-chartjs-2'
import { cssVar } from '../lib/charts'
import { api } from '../lib/api'
import { useApi } from '../lib/useApi'
import type { CatalogDashboard, SalesDashboard, CodResponse, InsightsResponse, DiagnosisResponse, MetricsResponse } from '../lib/api'
import { Sheet } from '../components/Paper'
import { Loading } from '../components/PageHeader'
import { Stat } from '../components/Stat'
import { StaleStockAlert } from '../components/StaleStockAlert'

type Tab = 'today' | 'inventory' | 'catalog' | 'sales' | 'customers'

export function Dashboard() {
  const [tab, setTab] = useState<Tab>('today')

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '.8rem' }}>
        <span className="font-type" style={{ color: 'var(--red)', fontWeight: 700 }}>Анализи</span>
        <span style={{ color: 'var(--pencil)', fontSize: '.85rem' }}>числата на бизнеса</span>
      </div>

      {/* file-folder tabs */}
      <div style={{ display: 'flex', gap: '.4rem' }}>
        {([['today', 'Днес'], ['inventory', 'Инвентар'], ['catalog', 'Каталог'], ['sales', 'Продажби'], ['customers', 'Клиенти']] as [Tab, string][]).map(([k, label]) => (
          <button key={k} onClick={() => setTab(k)}
            className="font-type"
            style={{
              padding: '.4rem .95rem', fontSize: '.8rem', cursor: 'pointer',
              border: '1px solid var(--line)', borderBottom: 'none',
              borderRadius: '6px 6px 0 0',
              background: tab === k ? 'var(--paper)' : 'var(--manila)',
              color: tab === k ? 'var(--ink)' : 'var(--pencil)',
              boxShadow: tab === k ? '0 -2px 6px rgba(0,0,0,.06)' : 'none',
            }}>
            {label}
          </button>
        ))}
      </div>

      {/* mode="sync": mount the new tab immediately rather than waiting on the
          outgoing tab's exit animation (which can stall in a backgrounded tab). */}
      <AnimatePresence mode="sync">
        <motion.div key={tab}
          initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -8 }}
          transition={{ duration: 0.25 }}>
          {tab === 'today' ? <TodayTab /> : tab === 'inventory' ? <InventoryTab />
            : tab === 'catalog' ? <CatalogTab />
            : tab === 'sales' ? <SalesTab /> : <CustomersTab />}
        </motion.div>
      </AnimatePresence>
    </div>
  )
}

// -------------------------------------------------- Днес (the operator cockpit) ---
// The landing answers "what needs my attention now / how much money is at risk" — not the
// catalog-project stats. Reuses the badge counts + COD summary already computed elsewhere.
// Tone fires only when a value crosses zero (Stat's conditional salience) so a calm day stays calm.
function TodayTab() {
  const { data: badges } = useApi<Record<string, number>>('/api/summary/badges')
  const { data: cod } = useApi<CodResponse>('/api/cod')
  if (!badges || !cod) return <Sheet><Loading /></Sheet>

  const m = cod.summary
  const toShip = badges['/orders'] || 0
  const toSettle = badges['/cod'] || 0
  const drafts = badges['/intake'] || 0
  const allClear = toShip + toSettle + drafts === 0 && m.outstanding_amount === 0 && m.discrepancy_count === 0

  const tileLink = { textDecoration: 'none', color: 'inherit', display: 'block' } as const
  const grid = { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(9.5rem, 1fr))', gap: '.8rem' } as const

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      {allClear && (
        <div className="sticky-note" style={{ transform: 'rotate(-1deg)', width: 'fit-content', maxWidth: '30rem' }}>
          Чисто — няма спешни задачи. Щом дойдат поръчки, тук ще светнат парите в обращение,
          какво чака етикет и какво е за разчет.
        </div>
      )}

      <section>
        <div className="eyebrow" style={{ marginBottom: '.4rem' }}>Пари в риск</div>
        <div style={grid}>
          <Stat label="Изходящ НП" value={m.outstanding_amount} money instant hint="в обращение + за изплащане" />
          <Link to="/cod" style={tileLink}>
            <Stat label="За изплащане" value={m.awaiting_amount} money tone="blocked" instant hint="куриерите ни дължат" />
          </Link>
          <Link to="/cod" style={tileLink}>
            <Stat label="Разлики" value={m.discrepancy_amount} money tone="pending" instant hint={`${m.discrepancy_count} бр · събрано ≠ очаквано`} />
          </Link>
        </div>
      </section>

      {/* frozen capital in slow-moving stock — renders only when there IS stale stock */}
      <StaleStockAlert />

      <section>
        <div className="eyebrow" style={{ marginBottom: '.4rem' }}>За вършене</div>
        <div style={grid}>
          <Link to="/orders" style={tileLink}>
            <Stat label="За етикет" value={toShip} tone="pending" hint="поръчки чакат пратка" />
          </Link>
          <Link to="/cod" style={tileLink}>
            <Stat label="За разчет" value={toSettle} tone="pending" hint="доставени, за изплащане" />
          </Link>
          <Link to="/intake" style={tileLink}>
            <Stat label="Чернови за Barsy" value={drafts} tone="pending" hint="одобрени, за качване" />
          </Link>
        </div>
      </section>
    </div>
  )
}

// ---------------------------------------------------------------- Каталог (live) ---
function CatalogTab() {
  const [data, setData] = useState<CatalogDashboard | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    api.get<CatalogDashboard>('/api/dashboard/catalog').then(setData).catch((e) => setErr(String(e.message ?? e)))
  }, [])

  if (err) return <Sheet><p style={{ color: 'var(--red)' }}>Грешка при зареждане: {err}</p></Sheet>
  if (!data) return <Sheet><p className="pencil">зарежда…</p></Sheet>

  const c = data.cards
  const readyPct = c.total ? Math.round((c.launch_ready / c.total) * 100) : 0

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(9rem, 1fr))', gap: '.8rem' }}>
        <Stat label="Продукти" value={c.total} hint="в каталога" />
        <Stat label="Готови за старт" value={c.launch_ready} tone="ready" hint={`${readyPct}% от каталога`} />
        <Stat label="Блокирани" value={c.launch_blocked} tone="blocked" hint="с пречки" />
        <Stat label="Непроверени" value={c.unscored} tone="pending" hint="още не оценени" />
        <Stat label="Искат човек" value={c.needs_human} hint="за ръчен преглед" />
        <Stat label="Ср. цена" value={c.avg_retail} money hint="продажна" />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.3fr', gap: '1.1rem' }}>
        <Sheet tilt="l">
          <h3 style={{ fontSize: '1rem', marginBottom: '.2rem' }}>Готовност за старт</h3>
          <p style={{ color: 'var(--pencil)', fontSize: '.8rem', margin: '0 0 .8rem' }}>
            подготовка на 14-хилядния каталог
          </p>
          {/* a single segmented progress bar reads faster than a doughnut (no legend round-trip,
              less ink) and leads with the number that matters — % ready. */}
          <ReadinessBar readiness={data.readiness} readyPct={readyPct} />
        </Sheet>

        <Sheet tilt="r">
          <h3 style={{ fontSize: '1rem', marginBottom: '.2rem' }}>Продукти по доставчик</h3>
          <p style={{ color: 'var(--pencil)', fontSize: '.8rem', margin: '0 0 .8rem' }}>
            {c.suppliers} доставчика · снимка на цените {c.latest_snapshot ?? '—'}
          </p>
          <div role="img" aria-label={'Продукти по доставчик: ' + data.suppliers.map((s) => `${s.supplier} ${s.count}`).join(', ')}>
          <Bar
            data={{
              labels: data.suppliers.map((s) => s.supplier),
              datasets: [{
                label: 'продукти',
                data: data.suppliers.map((s) => s.count),
                backgroundColor: cssVar('--red'),
                borderRadius: 2,
              }],
            }}
            options={{
              indexAxis: 'y',
              plugins: { legend: { display: false } },
              scales: {
                x: { ticks: { color: cssVar('--pencil') }, grid: { color: cssVar('--line') } },
                y: { ticks: { color: cssVar('--ink'), font: { family: 'Courier New' } }, grid: { display: false } },
              },
            }} />
          </div>
        </Sheet>
      </div>
    </div>
  )
}

// ------------------------------------------------------- Продажби (empty until live) ---
function SalesTab() {
  const [data, setData] = useState<SalesDashboard | null>(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    api.get<SalesDashboard>('/api/dashboard/sales').then(setData).catch((e) => setErr(String(e.message ?? e)))
  }, [])

  if (err) return <Sheet><p style={{ color: 'var(--red)' }}>Грешка при зареждане: {err}</p></Sheet>
  if (!data) return <Sheet><p className="pencil">зарежда…</p></Sheet>

  const k = data.kpis
  const noSales = k.orders_total === 0

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      {noSales && (
        <div className="sticky-note" style={{ transform: 'rotate(-1deg)', width: 'fit-content', maxWidth: '32rem' }}>
          Магазинът още не е пуснат — 0 поръчки. Тези числа и графики ще се напълнят
          сами щом дойде първата поръчка от Shopify. Формата вече е готова.
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(9rem, 1fr))', gap: '.8rem' }}>
        <Stat label="Приход общо" value={k.revenue_total} money hint="от старта" />
        <Stat label="Приход днес" value={k.revenue_today} money />
        <Stat label="Поръчки" value={k.orders_total} hint="общо" />
        <Stat label="Продадени бройки" value={k.units_total} />
      </div>

      <Sheet tilt="l">
        <h3 style={{ fontSize: '1rem', marginBottom: '.2rem' }}>Топ продукти по приход</h3>
        {data.top_products.length === 0 ? (
          <EmptyRow text="Няма продажби още — тук ще излязат най-продаваните след старта." />
        ) : (
          <div role="img" aria-label={'Топ продукти по приход: ' + data.top_products.map((p) => `${p.name.slice(0, 24)} ${p.value}€`).join(', ')}>
          <Bar
            data={{
              labels: data.top_products.map((p) => p.name.slice(0, 24)),
              datasets: [{ label: 'приход', data: data.top_products.map((p) => p.value), backgroundColor: cssVar('--red'), borderRadius: 2 }],
            }}
            options={{ indexAxis: 'y', plugins: { legend: { display: false } } }} />
          </div>
        )}
      </Sheet>

      <Sheet tilt="r">
        <h3 style={{ fontSize: '1rem', marginBottom: '.2rem' }}>Ценови промени от доставчици</h3>
        <p style={{ color: 'var(--pencil)', fontSize: '.8rem', margin: '0 0 .6rem' }}>
          движения между последните две снимки на цените
        </p>
        {data.price_changes.length === 0 ? (
          <EmptyRow text="Нужни са поне 2 снимки на цените, за да се видят промени. Първата е направена днес." />
        ) : (
          <div style={{ display: 'grid', gap: '.4rem' }}>
            {data.price_changes.slice(0, 12).map((p) => (
              <div key={p.code} className="font-type" style={{
                display: 'flex', justifyContent: 'space-between', fontSize: '.8rem',
                borderBottom: '1px dashed var(--line)', paddingBottom: '.25rem',
              }}>
                <span>{p.name.slice(0, 40)}</span>
                <span style={{ color: p.pct < 0 ? 'var(--ready)' : 'var(--red)' }}>
                  {p.old}→{p.new} € ({p.pct > 0 ? '+' : ''}{p.pct}%)
                </span>
              </div>
            ))}
          </div>
        )}
      </Sheet>
    </div>
  )
}

// ---------------------------------------------- Клиенти (behavioral insights, no PII) ---
function CustomersTab() {
  const { data, error } = useApi<InsightsResponse>('/api/insights')
  if (error) return <Sheet><p style={{ color: 'var(--red)' }}>Грешка при зареждане: {error}</p></Sheet>
  if (!data) return <Sheet><Loading /></Sheet>
  const noData = data.orders === 0

  const barOpts = {
    indexAxis: 'y' as const, plugins: { legend: { display: false } },
    scales: { x: { ticks: { color: cssVar('--pencil') }, grid: { color: cssVar('--line') } },
      y: { ticks: { color: cssVar('--ink'), font: { family: 'Courier New' } }, grid: { display: false } } },
  }
  const c = data.customers

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      {noData && (
        <div className="sticky-note" style={{ transform: 'rotate(-1deg)', width: 'fit-content', maxWidth: '34rem' }}>
          Още няма поръчки — тези числа за клиентската база (нови/повторни, по градове, любими продукти,
          дял на наложения платеж, процент върнати) ще се напълнят сами щом магазинът тръгне. Без
          чувствителни данни — само поведение от поръчките.
        </div>
      )}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(9rem, 1fr))', gap: '.8rem' }}>
        <Stat label="Поръчки" value={data.orders} instant hint="в извадката" />
        <Stat label="Приход" value={data.revenue} money instant />
        <Stat label="Ср. поръчка" value={data.aov} money instant hint="AOV" />
        <Stat label="Клиенти" value={c.total} instant hint={`${c.returning} повторни`} />
        <Stat label="Повторни" value={c.repeat_rate} suffix="%" instant hint="лоялност" />
        <Stat label="Дял НП" value={data.cod_share} suffix="%" instant hint="наложен платеж" />
        <Stat label="Върнати (RTO)" value={data.delivery.rto_rate} suffix="%" tone={data.delivery.rto_rate > 15 ? 'blocked' : undefined} instant hint={`${data.delivery.refused} отказани`} />
      </div>

      {!noData && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.1rem' }}>
          <Sheet>
            <h3 style={{ fontSize: '1rem', marginBottom: '.5rem' }}>Топ градове</h3>
            <div role="img" aria-label={'Поръчки по град: ' + data.top_cities.map((x) => `${x.city} ${x.orders}`).join(', ')}>
              <Bar data={{ labels: data.top_cities.map((x) => x.city),
                datasets: [{ label: 'поръчки', data: data.top_cities.map((x) => x.orders), backgroundColor: cssVar('--red'), borderRadius: 2 }] }}
                options={barOpts} />
            </div>
          </Sheet>
          <Sheet>
            <h3 style={{ fontSize: '1rem', marginBottom: '.5rem' }}>Любими продукти</h3>
            <div role="img" aria-label={'Топ продукти: ' + data.top_products.map((x) => `${x.name} ${x.qty}`).join(', ')}>
              <Bar data={{ labels: data.top_products.map((x) => x.name),
                datasets: [{ label: 'бройки', data: data.top_products.map((x) => x.qty), backgroundColor: cssVar('--ready'), borderRadius: 2 }] }}
                options={barOpts} />
            </div>
          </Sheet>
        </div>
      )}
    </div>
  )
}

// ------------------------------------------- Инвентар · на живо от Barsy (main view) ---
// The go-forward inventory picture computed live from Barsy sales history + stock. Turns data into
// action: reorder list → PO builder, dead list → repricing. Empty/partial pre-launch (calm states).
function LiveInventory() {
  const { data, error } = useApi<MetricsResponse>('/api/inventory-health/metrics')
  if (error) return <Sheet><p style={{ color: 'var(--red)' }}>Грешка при зареждане: {error}</p></Sheet>
  if (!data) return <Sheet><Loading /></Sheet>

  const eur = (n: number) => n.toLocaleString('bg-BG', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' €'
  const k = data.kpi
  const num = (v: number | null): number | string => (v == null ? '—' : v)
  const noStock = k.skus === 0

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '.6rem' }}>
        <span className="eyebrow">Инвентар · на живо (Barsy)</span>
        <span style={{ color: 'var(--pencil)', fontSize: '.72rem' }}>
          {data.has_sales ? `скорости за ${data.window_days} дни` : 'основен изглед — попълва се от Barsy'}
        </span>
      </div>

      {noStock ? (
        <div className="sticky-note" style={{ transform: 'rotate(-.8deg)', width: 'fit-content', maxWidth: '34rem' }}>
          Barsy още няма заредена наличност — щом се качи стоката и тръгнат продажби, тук ще светнат
          оборот, дни-в-склад, застояла стока и какво е за поръчка. Формата е готова.
        </div>
      ) : !data.has_sales && (
        <div className="sticky-note" style={{ transform: 'rotate(-.6deg)', width: 'fit-content', maxWidth: '34rem' }}>
          Наличността е тук, но продажбена история още не се е натрупала — скоростите, оборотът и
          дните-в-склад ще се появят с първите продажби от Barsy.
        </div>
      )}

      {/* KPI tiles — turnover/DSI/GMROI show '—' until sales accrue */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(9rem, 1fr))', gap: '.8rem' }}>
        <Stat label="Оборот на склада" value={num(k.turnover, '×')} suffix={k.turnover == null ? '' : '×'} instant
          tone={k.turnover != null && k.turnover < 2 ? 'blocked' : undefined} hint="пъти/год. (здравословно 2–6×)" />
        <Stat label="Дни в склад (DSI)" value={num(k.dsi)} suffix={k.dsi == null ? '' : ' дни'} instant
          tone={k.dsi != null && k.dsi > 180 ? 'blocked' : undefined} hint="до продажба" />
        <Stat label="GMROI" value={num(k.gmroi)} instant
          tone={k.gmroi != null && k.gmroi < 1 ? 'blocked' : undefined} hint="€ марж на € в склада" />
        <Stat label="Застояла стока" value={k.dead_value} money instant tone={k.dead_n > 0 ? 'blocked' : undefined}
          hint={`${k.dead_pct}% · ${k.dead_n} арт. без продажба`} />
        <Stat label="Стойност склад" value={k.inv_value} money instant hint={`${k.skus} SKU`} />
        <Stat label="За поръчка" value={k.reorder_n} tone={k.reorder_n > 0 ? 'pending' : undefined}
          hint="под точката на презареждане" />
      </div>

      {/* Reorder alert → PO builder */}
      {data.reorder.length > 0 && (
        <section>
          <div className="eyebrow" style={{ marginBottom: '.4rem' }}>Пред изчерпване — за поръчка</div>
          <div style={{ background: 'var(--paper2)', border: '1px solid var(--pending)', borderLeft: '4px solid var(--pending)', borderRadius: 3, padding: '.9rem 1.1rem' }}>
            <div style={{ fontSize: '.85rem', marginBottom: '.6rem' }}>
              <strong>{k.reorder_n}</strong> бързи артикула падат под точката на презареждане.
              <Link to="/purchasing" className="font-type" style={{ marginLeft: '.6rem', color: 'var(--red)', textDecoration: 'none', fontSize: '.78rem' }}>Създай заявка →</Link>
            </div>
            <div style={{ display: 'grid', gap: '.25rem' }}>
              {data.reorder.slice(0, 6).map((it) => (
                <div key={it.article_id} style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: '.6rem', fontSize: '.78rem', borderTop: '1px dashed var(--line)', paddingTop: '.25rem', alignItems: 'baseline' }}>
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={it.name}>{it.name || `#${it.article_id}`}</span>
                  <span style={{ color: 'var(--pending)', fontVariantNumeric: 'tabular-nums' }} title={`налични ${it.stock} бр`}>{it.days_of_cover ?? '—'} дни</span>
                  <span style={{ color: 'var(--ready)', fontVariantNumeric: 'tabular-nums' }} title="предложено количество за поръчка">поръчай {it.suggested_qty}</span>
                </div>
              ))}
            </div>
          </div>
        </section>
      )}

      {/* Live dead-stock (no sales in window) → repricing */}
      {data.dead.length > 0 && (
        <section>
          <div className="eyebrow" style={{ marginBottom: '.4rem' }}>Застояла стока (без продажба)</div>
          <div style={{ background: 'var(--paper2)', border: '1px solid var(--blocked)', borderLeft: '4px solid var(--blocked)', borderRadius: 3, padding: '.9rem 1.1rem' }}>
            <div style={{ fontSize: '.85rem', marginBottom: '.6rem' }}>
              <strong>{eur(k.dead_value)}</strong> замразени в {k.dead_n} артикула без нито една продажба за периода.
            </div>
            <div style={{ display: 'grid', gap: '.25rem' }}>
              {data.dead.slice(0, 6).map((it) => (
                <div key={it.article_id} style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: '.6rem', fontSize: '.78rem', borderTop: '1px dashed var(--line)', paddingTop: '.25rem', alignItems: 'baseline' }}>
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={it.name}>{it.name || `#${it.article_id}`}</span>
                  <span style={{ color: 'var(--blocked)', fontVariantNumeric: 'tabular-nums' }}>{eur(it.value)}</span>
                  <Link to={`/pricing?q=${encodeURIComponent(it.name || String(it.article_id))}`} className="font-type" style={{ color: 'var(--red)', textDecoration: 'none', fontSize: '.72rem', whiteSpace: 'nowrap' }}>Отстъпка →</Link>
                </div>
              ))}
            </div>
          </div>
        </section>
      )}

      {/* Top sellers — keep in stock / feature */}
      {data.top_sellers.length > 0 && (
        <Sheet>
          <h3 style={{ fontSize: '1rem', marginBottom: '.5rem' }}>Най-продавани ({data.window_days} дни)</h3>
          <div style={{ display: 'grid', gap: '.25rem', fontSize: '.8rem' }}>
            {data.top_sellers.slice(0, 8).map((it) => (
              <div key={it.article_id} style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: '.6rem', borderBottom: '1px dashed var(--line)', paddingBottom: '.2rem', alignItems: 'baseline' }}>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={it.name}>{it.name || `#${it.article_id}`}</span>
                <span style={{ fontVariantNumeric: 'tabular-nums' }}>{it.units_sold} бр</span>
                <span style={{ color: it.days_of_cover != null && it.days_of_cover < 14 ? 'var(--pending)' : 'var(--pencil)', fontVariantNumeric: 'tabular-nums' }}>{it.days_of_cover ?? '—'} дни</span>
              </div>
            ))}
          </div>
        </Sheet>
      )}
    </div>
  )
}

// ---------------------------------------------------------- Инвентар (composed) ---
// LIVE Barsy metrics are the MAIN view; the old-ERP snapshot is reference-only, shown collapsed
// below. Owner directive: from now on we track with Barsy; the historical seed is just context.
function InventoryTab() {
  return (
    <div style={{ display: 'grid', gap: '1.3rem' }}>
      <LiveInventory />
      <details>
        <summary className="font-type" style={{ cursor: 'pointer', color: 'var(--pencil)', fontSize: '.82rem', padding: '.3rem 0' }}>
          📁 Историческо от стария софтуер (само за справка) — покажи
        </summary>
        <div style={{ marginTop: '.8rem' }}><HistoricalDiagnosis /></div>
      </details>
    </div>
  )
}

// ---- Историческа диагноза (стар ERP seed) — reference-only, EUR as-is, no conversion ----
function HistoricalDiagnosis() {
  const { data, error } = useApi<DiagnosisResponse>('/api/inventory-health/diagnosis')
  if (error) return <Sheet><p style={{ color: 'var(--red)' }}>Грешка при зареждане: {error}</p></Sheet>
  if (!data) return <Sheet><Loading /></Sheet>
  if (!data.available || !data.kpi) return (
    <Sheet><EmptyRow text="Историческата снимка още не е заредена. Ще се появи, щом добавим seed-данните." /></Sheet>
  )

  const eur = (n: number) => n.toLocaleString('bg-BG', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' €'
  const k = data.kpi
  const months = Object.keys(data.ts || {}).sort()
  const ts = data.ts || {}
  const abc = data.abc || {}
  const axis = {
    plugins: { legend: { display: true, labels: { color: cssVar('--ink'), font: { size: 11 } } } },
    scales: { x: { ticks: { color: cssVar('--pencil'), maxTicksLimit: 10 }, grid: { display: false } },
      y: { ticks: { color: cssVar('--pencil') }, grid: { color: cssVar('--line') } } },
  } as const

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <div className="sticky-note" style={{ transform: 'rotate(-.6deg)', width: 'fit-content', maxWidth: '40rem' }}>
        📁 Историческо от стария софтуер (Microinvest), {data.meta?.period_from}–{data.meta?.period_to}.
        Само за справка — това са минали данни. Живите числа за склада идват от Barsy (виж основния изглед горе).
      </div>

      {/* Диагноза на инвентара — the exception KPIs */}
      <section>
        <div className="eyebrow" style={{ marginBottom: '.4rem' }}>Диагноза на инвентара</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(9rem, 1fr))', gap: '.8rem' }}>
          <Stat label="Оборот на склада" value={k.turnover} suffix="×" instant
            tone={k.turnover < 2 ? 'blocked' : undefined} hint="здравословно 2–6× (пъти/год.)" />
          <Stat label="Дни в склад (DSI)" value={k.dsi} suffix=" дни" instant
            tone={k.dsi > 180 ? 'blocked' : undefined} hint="колко стои стока до продажба" />
          <Stat label="GMROI" value={k.gmroi} instant
            tone={k.gmroi < 1 ? 'blocked' : undefined} hint="€ марж на € в склада (>1 добре)" />
          <Stat label="Мъртва стока" value={k.dead_val} money instant tone="blocked"
            hint={`${k.dead_pct}% от склада · ${k.dead_n} арт.`} />
          <Stat label="Стойност склад" value={k.inv} money instant hint="по доставна цена" />
          <Stat label="Активни SKU" value={k.active} instant hint={`${k.reorder} за поръчка`} />
        </div>
      </section>

      {/* Тенденция — monthly revenue / cost / purchasing */}
      <Sheet tilt="l">
        <h3 style={{ fontSize: '1rem', marginBottom: '.2rem' }}>Тенденция по месеци</h3>
        <p style={{ color: 'var(--pencil)', fontSize: '.78rem', margin: '0 0 .6rem' }}>
          приход, себестойност и покупки — виждат се презапасяванията (покупки над приход)
        </p>
        <Line
          data={{
            labels: months,
            datasets: [
              { label: 'приход', data: months.map((m) => ts[m].rev), borderColor: cssVar('--red'), backgroundColor: cssVar('--red'), pointRadius: 0, borderWidth: 2, tension: 0.25 },
              { label: 'себестойност', data: months.map((m) => ts[m].cogs), borderColor: cssVar('--pencil'), backgroundColor: cssVar('--pencil'), pointRadius: 0, borderWidth: 1.5, tension: 0.25 },
              { label: 'покупки', data: months.map((m) => ts[m].purch), borderColor: cssVar('--ready'), backgroundColor: cssVar('--ready'), pointRadius: 0, borderWidth: 1.5, tension: 0.25 },
            ],
          }}
          options={axis} />
      </Sheet>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(19rem, 1fr))', gap: '1.1rem' }}>
        {/* ABC — revenue concentration */}
        <Sheet>
          <h3 style={{ fontSize: '1rem', marginBottom: '.2rem' }}>ABC анализ</h3>
          <p style={{ color: 'var(--pencil)', fontSize: '.78rem', margin: '0 0 .6rem' }}>
            A = топ 80% от приходите, C = опашката (кандидати за подрязване)
          </p>
          <Bar
            data={{
              labels: ['A', 'B', 'C'],
              datasets: [{ label: 'стойност', borderRadius: 2,
                data: ['A', 'B', 'C'].map((g) => abc[g]?.value || 0),
                backgroundColor: [cssVar('--ready'), cssVar('--pencil'), cssVar('--red')] }],
            }}
            options={{ plugins: { legend: { display: false } },
              scales: { y: { ticks: { color: cssVar('--pencil') }, grid: { color: cssVar('--line') } },
                x: { ticks: { color: cssVar('--ink') }, grid: { display: false } } } }} />
          <div style={{ display: 'flex', justifyContent: 'space-around', fontSize: '.74rem', color: 'var(--pencil)', marginTop: '.4rem' }}>
            {['A', 'B', 'C'].map((g) => (
              <span key={g}>{g}: {abc[g]?.count || 0} арт · {eur(abc[g]?.value || 0)}</span>
            ))}
          </div>
        </Sheet>

        {/* Доставчици — top suppliers, revenue + dead capital */}
        <Sheet>
          <h3 style={{ fontSize: '1rem', marginBottom: '.5rem' }}>Доставчици (топ по приход)</h3>
          <div style={{ display: 'grid', gap: '.25rem', fontSize: '.78rem', maxHeight: '18rem', overflowY: 'auto' }}>
            <div className="font-type" style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: '.6rem', color: 'var(--pencil)', fontSize: '.68rem', textTransform: 'uppercase', letterSpacing: '.04em' }}>
              <span>доставчик</span><span>приход</span><span>мъртва €</span>
            </div>
            {(data.suppliers || []).map((s) => (
              <div key={s.name} style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: '.6rem', borderBottom: '1px dashed var(--line)', paddingBottom: '.2rem', alignItems: 'baseline' }}>
                <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={s.name}>{s.name}</span>
                <span style={{ fontVariantNumeric: 'tabular-nums' }}>{eur(s.rev)}</span>
                <span style={{ fontVariantNumeric: 'tabular-nums', color: s.deadval > 0 ? 'var(--red)' : 'var(--pencil)' }}>{eur(s.deadval)}</span>
              </div>
            ))}
          </div>
        </Sheet>
      </div>

      {/* Мъртва стока — worst dead products by frozen capital */}
      <Sheet tilt="r">
        <h3 style={{ fontSize: '1rem', marginBottom: '.2rem' }}>Най-тежка мъртва стока</h3>
        <p style={{ color: 'var(--pencil)', fontSize: '.78rem', margin: '0 0 .6rem' }}>
          замразен капитал, непродавани; водят за по-голяма отстъпка или разпродажба
        </p>
        <div style={{ display: 'grid', gap: '.25rem', fontSize: '.78rem' }}>
          <div className="font-type" style={{ display: 'grid', gridTemplateColumns: '1fr 5rem 5rem 5rem', gap: '.5rem', color: 'var(--pencil)', fontSize: '.68rem', textTransform: 'uppercase', letterSpacing: '.04em' }}>
            <span>артикул</span><span>наличност</span><span>стойност</span><span>посл. продажба</span>
          </div>
          {(data.dead_products || []).slice(0, 25).map((p, idx) => (
            <div key={idx} style={{ display: 'grid', gridTemplateColumns: '1fr 5rem 5rem 5rem', gap: '.5rem', borderBottom: '1px dashed var(--line)', paddingBottom: '.2rem', alignItems: 'baseline' }}>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={p.name}>
                {p.name}{p.supplier && <span style={{ color: 'var(--pencil)' }}> · {p.supplier}</span>}
              </span>
              <span style={{ fontVariantNumeric: 'tabular-nums' }}>{p.stock}</span>
              <span style={{ fontVariantNumeric: 'tabular-nums', color: 'var(--red)' }}>{eur(p.stockval)}</span>
              <span style={{ fontVariantNumeric: 'tabular-nums', color: 'var(--pencil)' }}>{p.last || '—'}</span>
            </div>
          ))}
        </div>
      </Sheet>
    </div>
  )
}

function ReadinessBar({ readiness, readyPct }: {
  readiness: { label: string; value: number; tone: string }[]; readyPct: number
}) {
  const total = readiness.reduce((s, r) => s + r.value, 0) || 1
  return (
    <div>
      <div className="font-type" style={{ fontSize: '2rem', fontWeight: 700, lineHeight: 1 }}>
        {readyPct}%<span style={{ fontSize: '.85rem', color: 'var(--pencil)', fontWeight: 400 }}> готови за старт</span>
      </div>
      <div role="img" aria-label={readiness.map((r) => `${r.label}: ${r.value}`).join(', ')}
        style={{ display: 'flex', height: '1.1rem', borderRadius: 2, overflow: 'hidden', border: '1px solid var(--line)', marginTop: '.55rem' }}>
        {readiness.filter((r) => r.value > 0).map((r) => (
          <div key={r.label} title={`${r.label}: ${r.value}`}
            style={{ width: `${(r.value / total) * 100}%`, background: `var(--${r.tone})` }} />
        ))}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '.4rem 1rem', marginTop: '.55rem' }}>
        {readiness.map((r) => (
          <span key={r.label} className="font-type" style={{ fontSize: '.74rem', display: 'inline-flex', alignItems: 'center', gap: '.3rem' }}>
            <span style={{ width: '.6rem', height: '.6rem', borderRadius: 1, background: `var(--${r.tone})` }} />
            {r.label} · {r.value}
          </span>
        ))}
      </div>
    </div>
  )
}

function EmptyRow({ text }: { text: string }) {
  return (
    <div className="pencil" style={{
      border: '1px dashed var(--line)', borderRadius: 2, padding: '1.4rem',
      textAlign: 'center', fontSize: '.95rem',
    }}>{text}</div>
  )
}
