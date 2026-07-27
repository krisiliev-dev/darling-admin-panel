import { useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api, ApiError } from '../lib/api'
import type { PricingItem, SupplierRule, OnlinePriceItem } from '../lib/api'
import { useApi } from '../lib/useApi'
import { useHotkey, isTyping } from '../lib/useHotkey'
import { printLabels } from '../lib/label'
import { useAuth } from '../lib/auth'
import { Sheet } from '../components/Paper'
import { useConfirm } from '../components/Confirm'
import { Pager, usePager } from '../components/TableControls'
import { PageHeader, EmptyNote, Loading } from '../components/PageHeader'
import { ProductBulkPricing } from '../components/ProductBulkPricing'

// Ценообразуване — cost-plus-markup repricing. Retail should track supplier COST: buy €1.00 +50%
// → sell €1.50; when the supplier cost rises to €1.50, the shelf price should become €2.25. The
// panel proposes; the OWNER applies (writing the price to Barsy is owner-only).
export function Pricing({ embedded = false }: { embedded?: boolean } = {}) {
  const { me } = useAuth()
  const isOwner = !!me?.is_superuser
  // deep-link: /pricing?q=<name> jumps straight to an article (e.g. from the stale-stock "Отстъпка"
  // button). When deep-linked we drop the flagged-only filter so the item shows even if it isn't
  // currently flagged for reprice.
  const [params] = useSearchParams()
  const deepQ = params.get('q') || ''
  const deepMd = params.get('md') || ''  // suggested clearance % carried from the stale-stock alert
  const [q, setQ] = useState(deepQ)
  const [submitted, setSubmitted] = useState(deepQ)
  const [flaggedOnly, setFlaggedOnly] = useState(!deepQ)
  const searchRef = useRef<HTMLInputElement>(null)
  useHotkey((e) => e.key === '/' && !isTyping(e), () => searchRef.current?.focus())

  const path = `/api/pricing/review?only_flagged=${flaggedOnly}&q=${encodeURIComponent(submitted)}`
  const { data, error, loading, reload } = useApi<{ items: PricingItem[] }>(path, [flaggedOnly, submitted])
  const pg = usePager(data?.items || [], 40)
  const [msg, setMsg] = useState('')

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      {!embedded && <PageHeader title="Ценообразуване" subtitle="доставна цена + надценка → продажна" />}

      {isOwner && <ProductBulkPricing />}
      {isOwner && <OnlinePriceList />}
      {isOwner && <SupplierRules />}

      {deepMd && Number(deepMd) > 0 && (
        <div className="sticky-note" style={{ transform: 'rotate(-.6deg)', width: 'fit-content', maxWidth: '32rem' }}>
          💡 Застояла стока: препоръчана клирънс отстъпка <strong>−{deepMd}%</strong> от продажната цена,
          за да се освободи склад. Прецени и приложи ръчно.
        </div>
      )}

      <Sheet clip="left">
        <div style={{ display: 'flex', gap: '.5rem', alignItems: 'center', flexWrap: 'wrap', marginBottom: '.7rem' }}>
          <input ref={searchRef} className="input-paper" style={{ width: '14rem' }} placeholder="търси по име или код  ( / )"
            aria-label="търсене на артикул" value={q} onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter') setSubmitted(q) }} />
          <button className="btn-paper" onClick={() => setSubmitted(q)}>Търси</button>
          <label className="font-type" style={{ fontSize: '.78rem', display: 'flex', gap: '.3rem', alignItems: 'center', marginLeft: '.4rem' }}>
            <input type="checkbox" checked={flaggedOnly} onChange={(e) => setFlaggedOnly(e.target.checked)} />
            само за преоценка
          </label>
        </div>

        {msg && <div className="font-type" style={{ fontSize: '.8rem', marginBottom: '.5rem', color: 'var(--ready)' }}>{msg}</div>}
        {error && <p style={{ color: 'var(--red)', fontFamily: 'var(--type)', fontSize: '.82rem' }}>Грешка: {error}</p>}
        {loading && !data && <Loading />}
        {data && data.items.length === 0 && (
          <EmptyNote>
            Няма артикули за преоценка. Тук се появяват продуктите, чиято продажна цена вече не отговаря
            на доставната × надценката — обикновено защото доставчикът е сменил цената. Задай надценка на
            артикул и панелът ще следи цената вместо теб. Доставните цени идват от Barsy при зареждане.
          </EmptyNote>
        )}
        {data && data.items.length > 0 && (
          <>
            <div className="font-type" style={{ display: 'grid', gridTemplateColumns: '1fr 5rem 5rem 5rem 6rem 5rem', gap: '.5rem', fontSize: '.6rem', textTransform: 'uppercase', color: 'var(--red)', letterSpacing: '.06em', paddingBottom: '.2rem', borderBottom: '1px solid var(--line)' }}>
              <span>Артикул</span><span style={{ textAlign: 'right' }}>Доставна</span><span style={{ textAlign: 'right' }}>Продажна</span>
              <span style={{ textAlign: 'right' }}>Надценка</span><span style={{ textAlign: 'right' }}>Предложена</span><span></span>
            </div>
            {pg.slice.map((it) => (
              <PriceRow key={it.article_id} it={it} isOwner={isOwner}
                onChanged={(m) => { setMsg(m); reload() }} />
            ))}
            <Pager {...pg} />
          </>
        )}
      </Sheet>

      {!isOwner && (
        <p className="font-type" style={{ fontSize: '.76rem', color: 'var(--pencil)' }}>
          Задаването на надценка е позволено; прилагането на нова цена в Barsy е само за собственика.
        </p>
      )}
    </div>
  )
}

function PriceRow({ it, isOwner, onChanged }: { it: PricingItem; isOwner: boolean; onChanged: (m: string) => void }) {
  // markup shown as a whole-number percent; empty until a rule is set
  const [pct, setPct] = useState(it.target_markup != null ? String(Math.round(it.target_markup * 100)) : '')
  const [busy, setBusy] = useState('')
  const confirm = useConfirm()

  const saveMarkup = async () => {
    const v = parseFloat(pct)
    if (!(v >= 0)) { onChanged('Надценката трябва да е число ≥ 0.'); return }
    setBusy('rule')
    try {
      await api.post('/api/pricing/rule', { article_id: it.article_id, markup_pct: v / 100 })
      onChanged('Надценката е запазена.')
    } catch (e) { onChanged(e instanceof ApiError ? e.message : 'Грешка при запис.') } finally { setBusy('') }
  }

  const apply = async () => {
    const proposed = it.proposed_retail?.toFixed(2)
    if (!(await confirm({ confirmLabel: 'Приложи', title: 'Нова продажна цена', message: `„${it.name}“:\n${it.retail?.toFixed(2) ?? '—'} → ${proposed} €\n(доставна ${it.cost.toFixed(2)} + ${Math.round((it.target_markup ?? 0) * 100)}%)\n\nТова записва цената в Barsy.` }))) return
    setBusy('apply')
    try {
      const r = await api.post<{ new_retail: number }>('/api/pricing/apply', { article_id: it.article_id, confirm: true })
      onChanged(`Цената е обновена на ${r.new_retail.toFixed(2)} €.`)
    } catch (e) { onChanged(e instanceof ApiError ? e.message : 'Грешка при прилагане.') } finally { setBusy('') }
  }

  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '1fr 5rem 5rem 5rem 6rem 5rem', gap: '.5rem', alignItems: 'center',
      fontSize: '.82rem', padding: '.35rem 0', borderBottom: '1px dashed var(--line)',
      borderLeft: `3px solid ${it.needs_apply ? 'var(--pending)' : 'transparent'}`, paddingLeft: '.5rem',
    }}>
      <span>{it.name} <span style={{ color: 'var(--pencil)' }}>{it.code}</span>
        {it.markup_source === 'supplier' && <span className="font-type" style={{ color: 'var(--pencil)', fontSize: '.6rem', marginLeft: '.3rem' }} title={it.supplier}>по доставчик</span>}
        {it.cost_changed && <span className="font-type" style={{ color: 'var(--pending)', fontSize: '.6rem', marginLeft: '.3rem' }}>↑ смяна на доставна</span>}
      </span>
      <span className="font-type" style={{ textAlign: 'right' }}>{it.cost.toFixed(2)}</span>
      <span className="font-type" style={{ textAlign: 'right', color: 'var(--pencil)' }}>{it.retail != null ? it.retail.toFixed(2) : '—'}</span>
      <span style={{ textAlign: 'right', display: 'flex', gap: '.15rem', alignItems: 'center', justifyContent: 'flex-end' }}>
        <input className="input-paper" inputMode="decimal" aria-label={`надценка за ${it.name}`}
          style={{ width: '3.2rem', textAlign: 'right', padding: '.2rem .3rem' }}
          value={pct} onChange={(e) => setPct(e.target.value)}
          onBlur={() => { if (pct !== (it.target_markup != null ? String(Math.round(it.target_markup * 100)) : '')) void saveMarkup() }}
          onKeyDown={(e) => { if (e.key === 'Enter') void saveMarkup() }} disabled={busy === 'rule'} />
        <span style={{ color: 'var(--pencil)', fontSize: '.7rem' }}>%</span>
      </span>
      <span className="font-type" style={{ textAlign: 'right', fontWeight: it.needs_apply ? 700 : 400, color: it.needs_apply ? 'var(--pending)' : 'var(--pencil)' }}>
        {it.proposed_retail != null ? it.proposed_retail.toFixed(2) + ' €' : '—'}
      </span>
      <span style={{ textAlign: 'right' }}>
        {it.needs_apply && isOwner && (
          <button className="btn-paper btn-red" style={{ fontSize: '.7rem' }} disabled={busy === 'apply'}
            onClick={() => void apply()}>{busy === 'apply' ? '…' : 'Приложи'}</button>
        )}
      </span>
    </div>
  )
}

// Онлайн цени — products that carry a distinct ONLINE (Shopify) price, set via the bulk toolbar
// above. Read-only overview: store price vs online price. The online price lives in a native Barsy
// rule scoped to the online delivery types; the physical store price is untouched. Clear one with
// „🌐 Онлайн цена → ⌫ изчисти" on its selection.
function OnlinePriceList() {
  const { data } = useApi<{ items: OnlinePriceItem[]; count: number }>('/api/online-pricing')
  const items = data?.items || []
  const eur = (n: number | null) => n == null ? '—' : n.toLocaleString('bg-BG', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' €'
  return (
    <Sheet>
      <h3 style={{ fontSize: '.95rem', marginBottom: '.4rem' }}>🌐 Онлайн цени ({items.length})</h3>
      {items.length === 0 ? (
        <p className="font-type" style={{ color: 'var(--pencil)', fontSize: '.8rem' }}>
          Няма зададени онлайн цени. Избери продукти горе и „🌐 Онлайн цена“ — важи само за онлайн
          поръчки (до адрес/офис); магазинната цена остава.
        </p>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table className="font-type" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.78rem' }}>
            <thead><tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--red)', textAlign: 'left' }}>
              <th style={{ padding: '.25rem' }}>Продукт</th><th style={{ padding: '.25rem' }}>Код</th>
              <th style={{ padding: '.25rem', textAlign: 'right' }}>Магазин</th>
              <th style={{ padding: '.25rem', textAlign: 'right' }}>Онлайн</th>
            </tr></thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.article_id} style={{ borderBottom: '1px dashed var(--line)' }}>
                  <td style={{ padding: '.3rem .25rem' }}>{it.name || `#${it.article_id}`}</td>
                  <td style={{ padding: '.3rem .25rem', color: 'var(--pencil)' }}>{it.code}</td>
                  <td style={{ padding: '.3rem .25rem', textAlign: 'right' }}>{eur(it.store_price)}</td>
                  <td style={{ padding: '.3rem .25rem', textAlign: 'right', fontWeight: 700, color: 'var(--red)' }}>{it.online_price} €</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Sheet>
  )
}

// Manager-only: markup rules by SUPPLIER (Mira-N 50%, Vertex 60%, …) + bulk apply. A product's own
// rule always wins over its supplier's. Applying writes prices to Barsy for the whole supplier.
function SupplierRules() {
  const { data, reload } = useApi<{ suppliers: SupplierRule[] }>('/api/pricing/suppliers')
  const [msg, setMsg] = useState('')

  return (
    <Sheet tilt="l">
      <h3 style={{ fontSize: '1rem' }}>Правила по доставчик</h3>
      <p style={{ color: 'var(--pencil)', fontSize: '.8rem', margin: '.2rem 0 0' }}>
        надценка за цял доставчик; продукт със собствена надценка има предимство
      </p>
      {msg && <div className="font-type" style={{ fontSize: '.8rem', color: 'var(--ready)', marginTop: '.4rem' }}>{msg}</div>}
      <div style={{ display: 'grid', gap: '.3rem', marginTop: '.6rem', maxHeight: '20rem', overflowY: 'auto' }}>
        {!data ? <span className="pencil" style={{ fontSize: '.82rem' }}>зарежда…</span>
          : data.suppliers.map((s) => <SupplierRow key={s.name} s={s} onChanged={(m) => { setMsg(m); reload() }} />)}
      </div>
    </Sheet>
  )
}

function SupplierRow({ s, onChanged }: { s: SupplierRule; onChanged: (m: string) => void }) {
  const [pct, setPct] = useState(s.markup_pct != null ? String(Math.round(s.markup_pct * 100)) : '')
  const [busy, setBusy] = useState('')
  const confirm = useConfirm()

  const save = async () => {
    const v = parseFloat(pct)
    if (!(v >= 0)) { onChanged('Надценката трябва да е число ≥ 0.'); return }
    setBusy('save')
    try { await api.post('/api/pricing/group', { scope: 'supplier', key: s.name, markup_pct: v / 100 }); onChanged(`Надценката за „${s.name}“ е запазена.`) }
    catch (e) { onChanged(e instanceof ApiError ? e.message : 'Грешка.') } finally { setBusy('') }
  }
  const applyAll = async () => {
    if (!(await confirm({ danger: true, confirmLabel: 'Приложи на всички', title: 'Надценка за целия доставчик', message: `Приложи ${pct}% надценка към ВСИЧКИ продукти на „${s.name}“ (${s.product_count} бр.)?\nТова презаписва продажните им цени в Barsy (продуктите със собствена надценка запазват своята).` }))) return
    setBusy('apply')
    try {
      const r = await api.post<{ applied: number; skipped: number; changed?: { name: string; price: number | null; barcode: string; code: string }[] }>('/api/pricing/apply-supplier', { supplier: s.name, confirm: true })
      onChanged(`„${s.name}“: обновени ${r.applied}, пропуснати ${r.skipped}.`)
      // legal: the new price must be on the shelf tag — offer to print fresh labels for the shop floor
      if (r.changed && r.changed.length && (await confirm({ confirmLabel: 'Отпечатай', message: `Цените са сменени. Отпечатай ${r.changed.length} нови етикета за магазина?` }))) {
        printLabels(r.changed.map((x) => ({ ...x, copies: 1 })))
      }
    } catch (e) { onChanged(e instanceof ApiError ? e.message : 'Грешка при прилагане.') } finally { setBusy('') }
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 4rem 5rem 8rem', gap: '.5rem', alignItems: 'center', fontSize: '.82rem', padding: '.25rem 0', borderBottom: '1px dashed var(--line)' }}>
      <span className="font-type">{s.name}</span>
      <span className="font-type" style={{ color: 'var(--pencil)', textAlign: 'right' }}>{s.product_count} бр.</span>
      <span style={{ display: 'flex', gap: '.15rem', alignItems: 'center', justifyContent: 'flex-end' }}>
        <input className="input-paper" inputMode="decimal" aria-label={`надценка за ${s.name}`}
          style={{ width: '3.2rem', textAlign: 'right', padding: '.2rem .3rem' }}
          value={pct} onChange={(e) => setPct(e.target.value)}
          onBlur={() => { if (pct !== (s.markup_pct != null ? String(Math.round(s.markup_pct * 100)) : '')) void save() }}
          onKeyDown={(e) => { if (e.key === 'Enter') void save() }} disabled={busy === 'save'} />
        <span style={{ color: 'var(--pencil)', fontSize: '.7rem' }}>%</span>
      </span>
      <button className="btn-paper btn-red" style={{ fontSize: '.7rem' }} disabled={!pct || busy === 'apply'} onClick={() => void applyAll()}>
        {busy === 'apply' ? '…' : 'Приложи за всички'}
      </button>
    </div>
  )
}
