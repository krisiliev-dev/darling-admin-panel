import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { motion } from 'framer-motion'
import { api, ApiError } from '../lib/api'
import type { CourierReadiness, Office, Order, OrdersResponse } from '../lib/api'
import { Sheet } from '../components/Paper'
import { PageHeader } from '../components/PageHeader'
import { CustomerRisk } from '../components/CustomerRisk'
import { useConfirm } from '../components/Confirm'
import { FilterChips, Pager, usePager } from '../components/TableControls'
import { Cod } from './Cod'
import { hoursSince, timeSinceBg, SLA_HOURS, SLA_WARN } from '../lib/time'

// A label-form prefill (from an order, or blank for an ad-hoc shipment).
export interface ShipPrefill {
  recipient: string; phone: string; city: string; street: string; cod_amount: number
}

// Онлайн поръчки — the fulfilment desk. One card, three tabs: orders to fulfil, orders already
// shipped, and COD reconciliation (Разчети). The "Нова пратка" flow (top-right) books an ad-hoc
// courier label — office lookup folded in. Courier readiness/config now lives in Настройки.
type OrdersTab = 'todo' | 'done' | 'cod'

export function Orders() {
  const [couriers, setCouriers] = useState<CourierReadiness[] | null>(null)
  const [data, setData] = useState<OrdersResponse | null>(null)
  const [prefill, setPrefill] = useState<ShipPrefill | null>(null)
  const [prefillVer, setPrefillVer] = useState(0)
  const [showCreate, setShowCreate] = useState(false)
  const [tab, setTab] = useState<OrdersTab>('todo')

  const loadOrders = useCallback(() => {
    api.get<OrdersResponse>('/api/orders?limit=100').then(setData).catch(() => setData({ orders: [], statuses: {} }))
  }, [])
  useEffect(() => {
    api.get<{ couriers: CourierReadiness[] }>('/api/orders/couriers')
      .then((d) => setCouriers(d.couriers)).catch(() => setCouriers([]))
    loadOrders()
  }, [loadOrders])

  // "Етикет" on an order opens the create-flow prefilled; the version bump re-applies the prefill
  const labelFromOrder = (o: Order) => {
    setPrefill({ recipient: o.customer, phone: o.phone, city: o.city, street: o.address, cod_amount: o.cod ? o.cod_amount : 0 })
    setPrefillVer((v) => v + 1)
    setShowCreate(true)
    document.getElementById('create-shipment')?.scrollIntoView({ behavior: 'smooth' })
  }
  const toggleCreate = () => {
    setShowCreate((s) => {
      const next = !s
      if (next) { setPrefill(null); setPrefillVer((v) => v + 1) }
      return next
    })
  }

  const tabs: [OrdersTab, string, number | null][] = [
    ['todo', 'За изпълнение', (data?.orders || []).filter((o) => !o.tracking).length],
    ['done', 'Изпълнени', (data?.orders || []).filter((o) => o.tracking).length],
    ['cod', 'Разчети', null],
  ]

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <PageHeader title="Онлайн поръчки" subtitle="изпълнение с етикет и наложен платеж" right={
        <button className="btn-paper btn-red" onClick={toggleCreate}>
          {showCreate ? '× Затвори' : '＋ Нова пратка'}
        </button>
      } />

      {/* create-shipment flow — hidden until invoked (top-right button or an order's Етикет) */}
      {showCreate && (
        <div id="create-shipment">
          <ShipmentTool couriers={couriers} prefill={prefill} prefillVer={prefillVer} onDone={() => setShowCreate(false)} />
        </div>
      )}

      {/* one card, three tabs */}
      <div style={{ display: 'flex', gap: '.3rem', flexWrap: 'wrap' }}>
        {tabs.map(([k, label, count]) => (
          <button key={k} className="font-type" onClick={() => setTab(k)} style={{
            padding: '.35rem .8rem', fontSize: '.78rem', cursor: 'pointer', borderRadius: 2,
            border: '1px solid var(--line)', background: tab === k ? 'var(--red)' : 'var(--paper2)',
            color: tab === k ? '#fff' : 'var(--pencil)', display: 'inline-flex', gap: '.35rem', alignItems: 'center',
          }}>
            {label}
            {count != null && count > 0 && (
              <span style={{ background: tab === k ? 'rgba(255,255,255,.25)' : 'var(--line)', borderRadius: 8, padding: '0 .35rem', fontSize: '.66rem' }}>{count}</span>
            )}
          </button>
        ))}
      </div>

      {tab === 'cod' ? <Cod embedded />
        : <OrderQueue data={data} mode={tab} onLabel={labelFromOrder} onChange={loadOrders} />}
    </div>
  )
}

// --------------------------------------------------------------------- order queue ---
type OrderFilter = 'all' | 'late' | 'cod'

function OrderQueue({ data, mode, onLabel, onChange }: { data: OrdersResponse | null; mode: 'todo' | 'done'; onLabel: (o: Order) => void; onChange: () => void }) {
  const [filter, setFilter] = useState<OrderFilter>('all')
  const confirm = useConfirm()
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')
  // Review actions: FINALISE (clientorder → closed sale, decrement, fiscal:0) or REFUSE (void +
  // cancel the Shopify order; nothing decremented). Both are real Barsy writes → confirm + gated backend.
  const act = async (o: Order, kind: 'finalise' | 'refuse') => {
    const q = kind === 'finalise'
      ? { title: 'Финализирай поръчката', confirmLabel: 'Финализирай',
          message: `Осчетоводяване на ${o.name} в Barsy — продажба + изписване на наличността.` }
      : { title: 'Откажи поръчката', confirmLabel: 'Откажи', danger: true,
          message: `Отказ на ${o.name} — количката се освобождава и нищо не се осчетоводява. (Използвай при съмнителен клиент.)` }
    if (!(await confirm(q))) return
    setBusy(o.id + kind); setMsg('')
    try {
      // o.id is a Shopify GID (gid://shopify/Order/123) — the slashes break a path param, so pass the
      // bare numeric id (also the key the connector's order-state uses).
      const oid = String(o.id).split('/').pop() || o.id
      const r = await api.post<{ status?: string; dry_run?: boolean; reason?: string }>(`/api/orders/${oid}/${kind}`, { confirm: true })
      const bg = kind === 'finalise' ? 'финализирана' : 'отказана'
      setMsg(r.dry_run ? `${o.name}: проба — Barsy запис е изключен (нищо не е записано).`
        : r.status === 'needs_review' ? `${o.name}: нужен преглед (${r.reason || '—'}) — виж Проблеми.`
        : `${o.name} ${bg}.`)
      onChange()
    } catch (e) { setMsg(e instanceof ApiError ? e.message : 'Грешка.') } finally { setBusy('') }
  }
  // todo = still needs a label; done = already shipped (has tracking)
  const base = (data?.orders || []).filter((o) => (mode === 'todo' ? !o.tracking : !!o.tracking))
  const isLate = (o: Order) => !o.tracking && hoursSince(o.created) > SLA_HOURS
  const preds: Record<OrderFilter, (o: Order) => boolean> = {
    all: () => true, late: isLate, cod: (o) => o.cod,
  }
  const chips: { key: OrderFilter; label: string; count: number }[] = [
    { key: 'all', label: 'всички', count: base.length },
    ...(mode === 'todo' ? [{ key: 'late' as const, label: 'закъснели', count: base.filter(isLate).length }] : []),
    { key: 'cod', label: 'НП', count: base.filter(preds.cod).length },
  ]
  const filtered = base.filter(preds[filter])
  const pg = usePager(filtered, 25)
  const lateCount = base.filter(isLate).length

  if (!data) return <Sheet><p className="pencil">зарежда поръчките…</p></Sheet>

  return (
    <Sheet clip="right">
      <div style={{ display: 'flex', gap: '.6rem', alignItems: 'baseline', marginBottom: '.4rem' }}>
        <h3 style={{ fontSize: '1rem', flex: 1 }}>{mode === 'todo' ? 'За изпълнение' : 'Изпълнени пратки'}</h3>
        {mode === 'todo' && lateCount > 0 && (
          <span className="font-type" style={{ fontSize: '.72rem', color: 'var(--red)', fontWeight: 700 }}>
            ⚠ {lateCount} закъснели (&gt;{SLA_HOURS}ч)
          </span>
        )}
      </div>
      {data.warning && (
        <div className="font-type" style={{ fontSize: '.8rem', marginBottom: '.5rem', color: 'var(--pending)' }}>⚠ {data.warning}</div>
      )}
      {base.length === 0 ? (
        mode === 'todo' ? (
          <div className="sticky-note" style={{ transform: 'rotate(-1deg)', width: 'fit-content', maxWidth: '34rem' }}>
            Няма поръчки за изпълнение — магазинът не е пуснат. Щом дойде първата поръчка от Shopify, тя
            застава тук: избираш куриер (Спиди/Еконт) → етикет с наложен платеж с един клик →
            проследяване. Куриерите се настройват в „Настройки“.
          </div>
        ) : (
          <p className="pencil" style={{ fontSize: '.85rem' }}>Още няма изпълнени пратки.</p>
        )
      ) : (
        <>
        <div style={{ marginBottom: '.5rem' }}><FilterChips options={chips} value={filter} onChange={setFilter} /></div>
        {msg && <div className="font-type" style={{ fontSize: '.8rem', marginBottom: '.5rem', color: msg.includes('Грешка') ? 'var(--red)' : 'var(--ink)' }}>{msg}</div>}
        <div style={{ display: 'grid', gap: '.4rem' }}>
          {pg.slice.map((o) => {
            const needsLabel = !o.tracking  // no label yet → still on our clock
            const age = needsLabel ? hoursSince(o.created) : 0
            const late = age > SLA_HOURS
            const warn = !late && age > SLA_WARN  // approaching the SLA breach
            const ageColor = late ? 'var(--red)' : warn ? 'var(--pending)' : 'var(--pencil)'
            return (
              <div key={o.id} style={{
                display: 'grid', gridTemplateColumns: '7rem 1fr auto auto auto', gap: '.7rem',
                alignItems: 'center', border: '1px solid var(--line)',
                borderLeft: `3px solid ${late ? 'var(--red)' : warn ? 'var(--pending)' : 'transparent'}`, borderRadius: 2,
                padding: '.5rem .7rem', background: late ? 'var(--paper)' : 'var(--paper2)',
              }}>
                <span className="font-type" style={{ fontWeight: 700 }}>{o.name}</span>
                <span>{o.customer} · {o.city}</span>
                {o.cod ? <span className="font-type" style={{ color: 'var(--red)', fontSize: '.8rem' }}>
                  НП {o.cod_amount.toFixed(2)} €</span> : <span className="pencil" style={{ fontSize: '.8rem' }}>предплатена</span>}
                <span style={{ display: 'grid', justifyItems: 'end', gap: '.05rem' }}>
                  <span className="font-type" style={{ fontSize: '.72rem', color: 'var(--pencil)' }}>{o.op_status_bg}</span>
                  <span className="font-type" title={o.created}
                    style={{ fontSize: '.62rem', color: ageColor, fontWeight: late || warn ? 700 : 400 }}>
                    {late ? '⚠ ' : warn ? '⏳ ' : ''}{timeSinceBg(o.created)}
                  </span>
                </span>
                {needsLabel && (
                  <span style={{ display: 'flex', gap: '.3rem', justifyContent: 'flex-end', flexWrap: 'wrap' }}>
                    <button className="btn-paper btn-red" style={{ fontSize: '.72rem' }}
                      disabled={busy === o.id + 'finalise'} onClick={() => act(o, 'finalise')}>Финализирай</button>
                    <button className="btn-paper" style={{ fontSize: '.72rem' }}
                      disabled={busy === o.id + 'refuse'} onClick={() => act(o, 'refuse')}>Откажи</button>
                    <button className="btn-paper" style={{ fontSize: '.72rem' }} onClick={() => onLabel(o)}>Етикет</button>
                  </span>
                )}
              </div>
            )
          })}
          {filtered.length === 0 && <p className="pencil" style={{ fontSize: '.82rem' }}>Няма поръчки по този филтър.</p>}
        </div>
        <Pager {...pg} />
        </>
      )}
    </Sheet>
  )
}

// ----------------------------------------------------------------- shipment / label ---
// The "Нова пратка" create-flow: books an ad-hoc courier label (Спиди/Еконт) for a phone/manual
// order, or from an order's Етикет button (prefill). Office lookup is folded in (no separate card).
function ShipmentTool({ couriers, prefill, prefillVer, onDone }: {
  couriers: CourierReadiness[] | null; prefill: ShipPrefill | null; prefillVer: number; onDone?: () => void
}) {
  const ready = (couriers || []).filter((c) => c.ready)
  const [courier, setCourier] = useState('')
  // numeric fields kept as strings so decimals ("2.5") can be typed; parsed at submit
  const [f, setF] = useState({ recipient: '', phone: '', city: '', street: '', office_id: '', weight_kg: '1', cod_amount: '0' })
  const [toOffice, setToOffice] = useState(false)
  const [officeLabel, setOfficeLabel] = useState('')
  // Дарлинг defaults (home goods) — pre-checked, but the packer can uncheck per shipment
  const [fragile, setFragile] = useState(true)
  const [inspection, setInspection] = useState(true)
  const [quote, setQuote] = useState<string>('')
  const [result, setResult] = useState<string>('')
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')

  // apply a prefill each time the create-flow is (re-)opened; prefillVer bumps even for the same order
  useEffect(() => {
    setResult(''); setQuote(''); setErr('')
    if (prefill) {
      setF((p) => ({ ...p, recipient: prefill.recipient, phone: prefill.phone, city: prefill.city, street: prefill.street, cod_amount: String(prefill.cod_amount) }))
    }
  }, [prefillVer]) // eslint-disable-line react-hooks/exhaustive-deps

  const onPickOffice = (p: OfficePick) => {
    setCourier(p.courier); setToOffice(true); setOfficeLabel(p.label)
    setF((prev) => ({ ...prev, office_id: p.office_id }))
    setResult(''); setQuote(''); setErr('')
  }

  const courierName = (c: string) =>
    ([...ready, { key: 'speedy', name: 'Спиди' }, { key: 'econt', name: 'Еконт' }].find((x) => x.key === c)?.name) || c

  const body = () => ({
    courier, recipient: f.recipient, phone: f.phone,
    weight_kg: parseFloat(f.weight_kg) || 0, cod_amount: parseFloat(f.cod_amount) || 0,
    office_id: toOffice ? f.office_id : null, city: f.city, street: f.street,
    fragile, inspection,
  })

  // validate instead of silently coercing bad numbers to 0 — a COD that becomes 0.00 is lost cash
  const validate = (): string => {
    if (!courier || !f.recipient.trim()) return 'Избери куриер и получател.'
    const w = parseFloat(f.weight_kg)
    if (!(w > 0)) return 'Теглото трябва да е положително число (кг).'
    if (f.cod_amount.trim() && !(parseFloat(f.cod_amount) >= 0)) return 'Наложеният платеж трябва да е число ≥ 0.'
    if (toOffice ? !f.office_id.trim() : !f.city.trim()) return 'Попълни офис или град за доставка.'
    return ''
  }

  const doQuote = async () => {
    const bad = validate(); if (bad) { setErr(bad); return }
    setBusy('quote'); setErr(''); setQuote('')
    try {
      const r = await api.post<{ price: number; currency: string }>('/api/orders/manual-quote', body())
      setQuote(`Ориентировъчна цена: ${r.price} ${r.currency}`)
    } catch (e) { setErr(e instanceof ApiError ? e.message : 'Грешка при оферта.') } finally { setBusy('') }
  }
  const confirm = useConfirm()
  const doLabel = async () => {
    const bad = validate(); if (bad) { setErr(bad); return }
    const b = body()
    const dest = toOffice ? `офис ${f.office_id}` : `${f.city}${f.street ? ', ' + f.street : ''}`
    const cod = b.cod_amount > 0 ? `НП ${b.cod_amount.toFixed(2)} €` : 'без наложен платеж'
    // restate the specifics — a bare yes/no on a real, money-booking action invites click-through
    if (!(await confirm({
      danger: true, confirmLabel: 'Създай етикет', title: 'Реален етикет при куриера',
      message: `${courierName(courier)} · ${f.recipient} · ${dest}\n${cod} · ${b.weight_kg} кг`
        + (quote ? `\n${quote}` : '') + `\n\nТова създава пратка и таксува.`,
    }))) return
    setBusy('label'); setErr(''); setResult('')
    try {
      const r = await api.post<{ tracking_number: string; label_ref: unknown }>('/api/orders/manual-label', { ...b, confirm: true })
      setResult(`Етикет създаден. Товарителница: ${r.tracking_number}`)
    } catch (e) { setErr(e instanceof ApiError ? e.message : 'Създаването се провали.') } finally { setBusy('') }
  }

  return (
    <Sheet clip="left" tilt="l">
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '.6rem' }}>
        <h3 style={{ fontSize: '1rem', marginBottom: '.2rem', flex: 1 }}>Нова пратка</h3>
        {onDone && <button className="btn-paper" style={{ fontSize: '.72rem' }} onClick={onDone}>затвори</button>}
      </div>
      <p style={{ color: 'var(--pencil)', fontSize: '.8rem', margin: '0 0 .7rem' }}>
        етикет със Спиди/Еконт (с наложен платеж) — за поръчка или ръчно (телефонна поръчка)
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(9rem, 1fr))', gap: '.5rem', marginBottom: '.5rem' }}>
        <Labeled label="куриер">
          <select className="input-paper" aria-label="куриер" value={courier} onChange={(e) => setCourier(e.target.value)}>
            <option value="">— куриер —</option>
            {(ready.length ? ready : [{ key: 'speedy', name: 'Спиди' }, { key: 'econt', name: 'Еконт' }]).map((c) => <option key={c.key} value={c.key}>{c.name}</option>)}
          </select>
        </Labeled>
        <Labeled label="получател"><input className="input-paper" aria-label="получател" placeholder="Иван Петров" value={f.recipient} onChange={(e) => setF({ ...f, recipient: e.target.value })} /></Labeled>
        <Labeled label="телефон"><input className="input-paper" aria-label="телефон" placeholder="08…" value={f.phone} onChange={(e) => setF({ ...f, phone: e.target.value })} /></Labeled>
        <Labeled label="тегло (кг)"><input className="input-paper" inputMode="decimal" aria-label="тегло в кг" value={f.weight_kg} onChange={(e) => setF({ ...f, weight_kg: e.target.value })} /></Labeled>
        <Labeled label="наложен платеж €"><input className="input-paper" inputMode="decimal" aria-label="наложен платеж в евро" value={f.cod_amount} onChange={(e) => setF({ ...f, cod_amount: e.target.value })} /></Labeled>
      </div>
      {/* COD-refuser risk check — advisory; staff decide whether to hold/require prepay */}
      {f.cod_amount && parseFloat(f.cod_amount) > 0 && f.phone.trim() && (
        <div style={{ marginBottom: '.5rem' }}><CustomerRisk phone={f.phone} /></div>
      )}
      <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', marginBottom: '.5rem' }}>
        <label className="font-type" style={{ fontSize: '.76rem', display: 'flex', gap: '.3rem', alignItems: 'center' }}>
          <input type="checkbox" checked={toOffice} onChange={(e) => { setToOffice(e.target.checked); if (!e.target.checked) setOfficeLabel('') }} /> до офис на куриера
        </label>
        <label className="font-type" style={{ fontSize: '.76rem', display: 'flex', gap: '.3rem', alignItems: 'center' }}>
          <input type="checkbox" checked={fragile} onChange={(e) => setFragile(e.target.checked)} /> чупливо
        </label>
        <label className="font-type" style={{ fontSize: '.76rem', display: 'flex', gap: '.3rem', alignItems: 'center' }}>
          <input type="checkbox" checked={inspection} onChange={(e) => setInspection(e.target.checked)} /> преглед
        </label>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(9rem, 1fr))', gap: '.5rem', marginBottom: '.6rem' }}>
        {toOffice ? (
          <Labeled label={officeLabel ? `офис: ${officeLabel}` : 'ID на офис'}>
            <input className="input-paper" aria-label="ID на офис" placeholder="ID на офис (или потърси долу)" value={f.office_id} onChange={(e) => { setF({ ...f, office_id: e.target.value }); setOfficeLabel('') }} />
          </Labeled>
        ) : (
          <>
            <Labeled label="град"><input className="input-paper" aria-label="град" placeholder="София" value={f.city} onChange={(e) => setF({ ...f, city: e.target.value })} /></Labeled>
            <Labeled label="адрес"><input className="input-paper" aria-label="адрес" placeholder="ул. …" value={f.street} onChange={(e) => setF({ ...f, street: e.target.value })} /></Labeled>
          </>
        )}
      </div>
      {/* office lookup folded into the flow — appears only when delivering to a courier office */}
      {toOffice && (
        <div style={{ marginBottom: '.6rem' }}>
          <OfficeSearch couriers={couriers} defaultKey={courier || 'speedy'} onPick={onPickOffice} />
        </div>
      )}
      <div style={{ display: 'flex', gap: '.6rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn-paper" onClick={() => void doQuote()} disabled={busy === 'quote'}>{busy === 'quote' ? 'смята…' : 'Оферта'}</button>
        <button className="btn-paper btn-red" onClick={() => void doLabel()} disabled={busy === 'label'}>{busy === 'label' ? 'създава…' : 'Създай етикет'}</button>
        {quote && <span className="font-type" style={{ fontSize: '.8rem', color: 'var(--pencil)' }}>{quote}</span>}
        {result && <span className="font-type" style={{ fontSize: '.8rem', color: 'var(--ready)' }}>{result}</span>}
        {err && <span className="font-type" style={{ fontSize: '.8rem', color: 'var(--red)' }}>{err}</span>}
      </div>
    </Sheet>
  )
}

// persistent field label (placeholders vanish on input; a label must not) — recognition over recall
function Labeled({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label style={{ display: 'grid', gap: '.15rem' }}>
      <span className="font-type" style={{ fontSize: '.6rem', textTransform: 'uppercase', letterSpacing: '.04em', color: 'var(--pencil)' }}>{label}</span>
      {children}
    </label>
  )
}

// office pick fills the shipment form directly — no ID transcription
interface OfficePick { courier: string; office_id: string; label: string }

// -------------------------------------------------------- inline office search (folded in) ---
// Compact, unwrapped (no Sheet) — lives inside the create-flow when delivering to a courier office.
function OfficeSearch({ couriers, defaultKey, onPick }: {
  couriers: CourierReadiness[] | null; defaultKey: string; onPick: (p: OfficePick) => void
}) {
  const configured = (couriers || []).filter((c) => c.configured)
  const [key, setKey] = useState(defaultKey)
  const [city, setCity] = useState('')
  const [offices, setOffices] = useState<Office[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => { if (configured.length && !configured.some((c) => c.key === key)) setKey(configured[0].key) }, [couriers]) // eslint-disable-line

  const search = async () => {
    setBusy(true); setErr(''); setOffices(null)
    try {
      const d = await api.get<{ offices: Office[]; count: number }>(
        `/api/orders/couriers/${key}/offices?city=${encodeURIComponent(city)}`)
      setOffices(d.offices)
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Грешка')
    } finally { setBusy(false) }
  }

  return (
    <div style={{ border: '1px dashed var(--line)', borderRadius: 3, padding: '.6rem .7rem', background: 'var(--paper2)' }}>
      <div className="font-type" style={{ fontSize: '.7rem', textTransform: 'uppercase', letterSpacing: '.04em', color: 'var(--pencil)', marginBottom: '.4rem' }}>
        търсене на офис / автомат
      </div>
      <div style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
        <select className="input-paper" style={{ width: 'auto' }} aria-label="куриер за търсене на офис" value={key} onChange={(e) => setKey(e.target.value)}>
          {(configured.length ? configured : [{ key: 'speedy', name: 'Спиди' }]).map((c) => (
            <option key={c.key} value={c.key}>{c.name}</option>
          ))}
        </select>
        <input className="input-paper" style={{ width: '12rem' }} placeholder="град (напр. София)" aria-label="град за търсене на офис"
          value={city} onChange={(e) => setCity(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') void search() }} />
        <button className="btn-paper" onClick={() => void search()} disabled={busy}>
          {busy ? 'търси…' : 'Търси офис'}
        </button>
      </div>
      {err && <p style={{ color: 'var(--red)', fontFamily: 'var(--type)', fontSize: '.8rem' }}>{err}</p>}
      {offices && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}
          style={{ marginTop: '.6rem', maxHeight: '16rem', overflowY: 'auto', display: 'grid', gap: '.3rem' }}>
          <div className="font-type" style={{ fontSize: '.72rem', color: 'var(--pencil)' }}>{offices.length} намерени</div>
          {offices.map((o) => (
            <div key={o.id} style={{
              display: 'flex', gap: '.5rem', alignItems: 'center',
              fontSize: '.8rem', borderBottom: '1px dashed var(--line)', paddingBottom: '.25rem',
            }}>
              <span style={{ flex: 1 }}>
                <span className="font-type" style={{ fontWeight: 700 }}>{o.name}</span>{' '}
                <span style={{ color: 'var(--pencil)' }}>— {o.address}</span>
              </span>
              <button className="btn-paper" style={{ fontSize: '.7rem', whiteSpace: 'nowrap' }}
                onClick={() => onPick({ courier: key, office_id: String(o.id), label: o.name })}>→ избери</button>
            </div>
          ))}
        </motion.div>
      )}
    </div>
  )
}
