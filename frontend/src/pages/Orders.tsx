import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { motion } from 'framer-motion'
import { api, ApiError } from '../lib/api'
import type { CourierReadiness, Office, Order, OrdersResponse } from '../lib/api'
import { Sheet } from '../components/Paper'
import { PageHeader } from '../components/PageHeader'
import { CourierBadge, courierTone } from '../components/CourierBadge'
import { CustomerRisk } from '../components/CustomerRisk'
import { FilterChips, Pager, usePager } from '../components/TableControls'
import { hoursSince, timeSinceBg, SLA_HOURS, SLA_WARN } from '../lib/time'

// A label-form prefill (from an order, or blank for an ad-hoc shipment).
export interface ShipPrefill {
  recipient: string; phone: string; city: string; street: string; cod_amount: number
}

// Поръчки — order fulfilment. Store isn't live (0 orders) so the queue shows an empty state;
// courier readiness, the office finder, and the ad-hoc shipment tool work today.
export interface OfficePick { courier: string; office_id: string; label: string }

export function Orders() {
  const [couriers, setCouriers] = useState<CourierReadiness[] | null>(null)
  const [data, setData] = useState<OrdersResponse | null>(null)
  const [prefill, setPrefill] = useState<ShipPrefill | null>(null)
  const [officePick, setOfficePick] = useState<OfficePick | null>(null)

  useEffect(() => {
    api.get<{ couriers: CourierReadiness[] }>('/api/orders/couriers')
      .then((d) => setCouriers(d.couriers)).catch(() => setCouriers([]))
    api.get<OrdersResponse>('/api/orders?limit=100').then(setData).catch(() => setData({ orders: [], statuses: {} }))
  }, [])

  const labelFromOrder = (o: Order) => setPrefill({
    recipient: o.customer, phone: o.phone, city: o.city, street: o.address,
    cod_amount: o.cod ? o.cod_amount : 0,
  })

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      <PageHeader title="Поръчки" subtitle="изпълнение с етикет и наложен платеж" />

      {/* inverted pyramid: the work queue is the payload, so it leads; couriers are context */}
      <OrderQueue data={data} onLabel={labelFromOrder} />
      <CourierStrip couriers={couriers} />
      <ShipmentTool couriers={couriers} prefill={prefill} officePick={officePick} />
      <OfficeFinder couriers={couriers} onPick={setOfficePick} />
    </div>
  )
}

// ---------------------------------------------------------------- courier readiness ---
function CourierStrip({ couriers }: { couriers: CourierReadiness[] | null }) {
  return (
    <Sheet tilt="l">
      <h3 style={{ fontSize: '1rem', marginBottom: '.6rem' }}>Куриери</h3>
      {!couriers ? <p className="pencil">проверява връзката…</p> : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(14rem, 1fr))', gap: '.7rem' }}>
          {couriers.map((c) => (
            <div key={c.key} style={{
              border: '1px solid var(--line)', borderLeft: `3px solid ${courierTone(c)}`,
              borderRadius: 2, padding: '.6rem .75rem', background: 'var(--paper2)',
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span className="font-type" style={{ fontWeight: 700 }}>{c.name}</span>
                <CourierBadge c={c} />
              </div>
              {c.note && <div style={{ fontSize: '.74rem', color: 'var(--pencil)', marginTop: '.2rem' }}>{c.note}</div>}
            </div>
          ))}
        </div>
      )}
    </Sheet>
  )
}

// --------------------------------------------------------------------- order queue ---
type OrderFilter = 'all' | 'label' | 'late' | 'cod'

function OrderQueue({ data, onLabel }: { data: OrdersResponse | null; onLabel: (o: Order) => void }) {
  const [filter, setFilter] = useState<OrderFilter>('all')
  const orders = data?.orders || []
  const isLate = (o: Order) => !o.tracking && hoursSince(o.created) > SLA_HOURS
  const preds: Record<OrderFilter, (o: Order) => boolean> = {
    all: () => true, label: (o) => !o.tracking, late: isLate, cod: (o) => o.cod,
  }
  const chips: { key: OrderFilter; label: string; count: number }[] = [
    { key: 'all', label: 'всички', count: orders.length },
    { key: 'label', label: 'за етикет', count: orders.filter(preds.label).length },
    { key: 'late', label: 'закъснели', count: orders.filter(isLate).length },
    { key: 'cod', label: 'НП', count: orders.filter(preds.cod).length },
  ]
  const filtered = orders.filter(preds[filter])
  const pg = usePager(filtered, 25)
  const lateCount = chips[2].count

  if (!data) return <Sheet><p className="pencil">зарежда поръчките…</p></Sheet>

  return (
    <Sheet clip="right">
      <div style={{ display: 'flex', gap: '.6rem', alignItems: 'baseline', marginBottom: '.4rem' }}>
        <h3 style={{ fontSize: '1rem', flex: 1 }}>Опашка за пратки</h3>
        {lateCount > 0 && (
          <span className="font-type" style={{ fontSize: '.72rem', color: 'var(--red)', fontWeight: 700 }}>
            ⚠ {lateCount} закъснели (&gt;{SLA_HOURS}ч)
          </span>
        )}
      </div>
      {data.warning && (
        <div className="font-type" style={{ fontSize: '.8rem', marginBottom: '.5rem', color: 'var(--pending)' }}>⚠ {data.warning}</div>
      )}
      {orders.length === 0 ? (
        <div className="sticky-note" style={{ transform: 'rotate(-1deg)', width: 'fit-content', maxWidth: '34rem' }}>
          Няма поръчки още — магазинът не е пуснат. Щом дойде първата поръчка от Shopify, тя
          застава тук: избираш куриер (Спиди/Еконт) → етикет с наложен платеж с един клик →
          проследяване. Куриерите горе вече са свързани.
        </div>
      ) : (
        <>
        <div style={{ marginBottom: '.5rem' }}><FilterChips options={chips} value={filter} onChange={setFilter} /></div>
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
                {needsLabel && <button className="btn-paper" style={{ fontSize: '.72rem' }} onClick={() => onLabel(o)}>Етикет</button>}
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
function ShipmentTool({ couriers, prefill, officePick }: { couriers: CourierReadiness[] | null; prefill: ShipPrefill | null; officePick: OfficePick | null }) {
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

  useEffect(() => {
    if (!prefill) return
    setF((p) => ({ ...p, recipient: prefill.recipient, phone: prefill.phone, city: prefill.city, street: prefill.street, cod_amount: String(prefill.cod_amount) }))
    setResult(''); setQuote('')
  }, [prefill])

  // picking an office in the finder below fills the form directly — no ID transcription
  useEffect(() => {
    if (!officePick) return
    setCourier(officePick.courier)
    setToOffice(true)
    setOfficeLabel(officePick.label)
    setF((p) => ({ ...p, office_id: officePick.office_id }))
    setResult(''); setQuote(''); setErr('')
  }, [officePick])

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
  const doLabel = async () => {
    const bad = validate(); if (bad) { setErr(bad); return }
    const b = body()
    const dest = toOffice ? `офис ${f.office_id}` : `${f.city}${f.street ? ', ' + f.street : ''}`
    const cod = b.cod_amount > 0 ? `НП ${b.cod_amount.toFixed(2)} €` : 'без наложен платеж'
    // restate the specifics — a bare yes/no on a real, money-booking action invites click-through
    if (!window.confirm(
      `Създай реален етикет при куриера:\n\n${courierName(courier)} · ${f.recipient} · ${dest}\n${cod} · ${b.weight_kg} кг`
      + (quote ? `\n${quote}` : '') + `\n\nТова създава пратка и таксува.`)) return
    setBusy('label'); setErr(''); setResult('')
    try {
      const r = await api.post<{ tracking_number: string; label_ref: unknown }>('/api/orders/manual-label', { ...b, confirm: true })
      setResult(`Етикет създаден. Товарителница: ${r.tracking_number}`)
    } catch (e) { setErr(e instanceof ApiError ? e.message : 'Създаването се провали.') } finally { setBusy('') }
  }

  return (
    <Sheet clip="left" tilt="l">
      <h3 style={{ fontSize: '1rem', marginBottom: '.2rem' }}>Създай пратка</h3>
      <p style={{ color: 'var(--pencil)', fontSize: '.8rem', margin: '0 0 .7rem' }}>
        етикет със Спиди/Еконт (с наложен платеж) — за поръчка или ръчно
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
            <input className="input-paper" aria-label="ID на офис" placeholder="ID на офис (или избери от долу)" value={f.office_id} onChange={(e) => { setF({ ...f, office_id: e.target.value }); setOfficeLabel('') }} />
          </Labeled>
        ) : (
          <>
            <Labeled label="град"><input className="input-paper" aria-label="град" placeholder="София" value={f.city} onChange={(e) => setF({ ...f, city: e.target.value })} /></Labeled>
            <Labeled label="адрес"><input className="input-paper" aria-label="адрес" placeholder="ул. …" value={f.street} onChange={(e) => setF({ ...f, street: e.target.value })} /></Labeled>
          </>
        )}
      </div>
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

// ------------------------------------------------------------------- office finder ---
function OfficeFinder({ couriers, onPick }: { couriers: CourierReadiness[] | null; onPick: (p: OfficePick) => void }) {
  const configured = (couriers || []).filter((c) => c.configured)
  const [key, setKey] = useState('speedy')
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
    <Sheet tilt="r">
      <h3 style={{ fontSize: '1rem', marginBottom: '.2rem' }}>Търсене на офис / автомат</h3>
      <p style={{ color: 'var(--pencil)', fontSize: '.8rem', margin: '0 0 .7rem' }}>
        за доставка до офис — избери куриер и град
      </p>
      <div style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
        <select className="input-paper" style={{ width: 'auto' }} aria-label="куриер за търсене на офис" value={key} onChange={(e) => setKey(e.target.value)}>
          {(configured.length ? configured : [{ key: 'speedy', name: 'Спиди' }]).map((c) => (
            <option key={c.key} value={c.key}>{c.name}</option>
          ))}
        </select>
        <input className="input-paper" style={{ width: '12rem' }} placeholder="град (напр. София)"
          value={city} onChange={(e) => setCity(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') void search() }} />
        <button className="btn-paper btn-red" onClick={() => void search()} disabled={busy}>
          {busy ? 'търси…' : 'Търси'}
        </button>
      </div>
      {err && <p style={{ color: 'var(--red)', fontFamily: 'var(--type)', fontSize: '.8rem' }}>{err}</p>}
      {offices && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}
          style={{ marginTop: '.8rem', maxHeight: '18rem', overflowY: 'auto', display: 'grid', gap: '.3rem' }}>
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
              {/* fill the shipment form directly — no ID transcription */}
              <button className="btn-paper" style={{ fontSize: '.7rem', whiteSpace: 'nowrap' }}
                onClick={() => onPick({ courier: key, office_id: String(o.id), label: o.name })}>→ избери</button>
            </div>
          ))}
        </motion.div>
      )}
    </Sheet>
  )
}
