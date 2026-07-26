import { useRef, useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { CodOrder, CodResponse, PayoutPreview } from '../lib/api'
import { useApi } from '../lib/useApi'
import { hoursSince } from '../lib/time'
import { Sheet } from '../components/Paper'
import { Stat } from '../components/Stat'
import { PageHeader, EmptyNote, Loading } from '../components/PageHeader'
import { FilterChips, Pager, usePager } from '../components/TableControls'

// Разчети — COD (наложен платеж) reconciliation. Tracks what couriers collected vs paid out.
const BUCKET_TONE: Record<string, string> = {
  preship: 'var(--pencil)', in_transit: 'var(--pending)',
  awaiting: 'var(--red)', settled: 'var(--ready)', rto: 'var(--pencil)',
}

export function Cod({ embedded = false }: { embedded?: boolean } = {}) {
  const { data, error: err, reload } = useApi<CodResponse>('/api/cod')
  const [busy, setBusy] = useState('')
  const [msg, setMsg] = useState('')

  const syncTracking = async () => {
    setBusy('sync'); setMsg('')
    try {
      const r = await api.post<{ delivered: number }>('/api/cod/sync-tracking')
      setMsg(`Проследяването е синхронизирано — ${r.delivered} нови доставени.`)
      reload()
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : 'Синхронизацията се провали.')
    } finally { setBusy('') }
  }

  // amounts imported from a courier payout file, keyed by order_id — pre-fills the settle form
  const [prefill, setPrefill] = useState<Record<string, number> | null>(null)
  const [prefillVer, setPrefillVer] = useState(0)
  const applyPrefill = (p: Record<string, number>) => { setPrefill(p); setPrefillVer((v) => v + 1) }

  const s = data?.summary
  const awaiting = (data?.orders || []).filter((o) => o.bucket === 'awaiting')

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      {!embedded && <PageHeader title="Разчети" subtitle="наложен платеж — събрано и изплатено" />}

      {s && (
        <>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(9rem, 1fr))', gap: '.8rem' }}>
            <Stat label="Изходящ НП" value={s.outstanding_amount} money instant hint="в обращение + за изплащане" />
            <Stat label="За изплащане" value={s.awaiting_amount} money tone="blocked" instant hint={`${s.awaiting_count} поръчки`} />
            <Stat label="Разчетено" value={s.settled_amount} money tone="ready" instant hint={`${s.settled_count} поръчки`} />
            {/* money, not a bare count — a 12-стотинки rounding gap ≠ a €40 short-payment */}
            <Stat label="Разлики" value={s.discrepancy_amount} money tone="pending" instant hint={`${s.discrepancy_count} бр · събрано ≠ очаквано`} />
          </div>
          <CodPipeline s={s} />
        </>
      )}

      <Sheet clip="left">
        <div style={{ display: 'flex', gap: '.6rem', alignItems: 'center', marginBottom: '.6rem', flexWrap: 'wrap' }}>
          <h3 style={{ fontSize: '1rem', flex: 1 }}>Поръчки с наложен платеж</h3>
          <button className="btn-paper" disabled={busy === 'sync'} onClick={() => void syncTracking()}>
            {busy === 'sync' ? 'синхронизира…' : 'Синхронизирай проследяване'}
          </button>
        </div>
        {msg && <div className="font-type" style={{ fontSize: '.8rem', marginBottom: '.5rem' }}>{msg}</div>}
        {err && <p style={{ color: 'var(--red)', fontFamily: 'var(--type)', fontSize: '.82rem' }}>Грешка: {err}</p>}
        {!data && !err && <Loading />}
        {data && data.orders.length === 0 && (
          <EmptyNote>
            Няма поръчки с наложен платеж още. Наложеният платеж е 55–60% от поръчките в България —
            щом магазинът тръгне, събраното от куриера ще се проследява тук: в обращение → за
            изплащане → разчетено, с маркиране на разлики.
          </EmptyNote>
        )}
        {data && data.orders.length > 0 && <CodTable orders={data.orders} />}
      </Sheet>

      {awaiting.length > 0 && <PayoutImport onApply={applyPrefill} />}

      {awaiting.length > 0 && (
        <SettlePanel
          key={awaiting.map((o) => o.order_id).join(',') + '#' + prefillVer}
          orders={awaiting} prefill={prefill} onDone={reload} />
      )}
    </div>
  )
}

// upload a courier payout file (Speedy/Econt) → preview the match, then pre-fill the settle form
function PayoutImport({ onApply }: { onApply: (p: Record<string, number>) => void }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [preview, setPreview] = useState<PayoutPreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [courier, setCourier] = useState('speedy')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')

  const onFile = async (file: File) => {
    setBusy(true); setErr(''); setPreview(null)
    try {
      const form = new FormData()
      form.append('file', file)
      const p = await api.upload<PayoutPreview>('/api/cod/import-payout', form)
      setPreview(p)
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Файлът не можа да се прочете.')
    } finally { setBusy(false); if (inputRef.current) inputRef.current.value = '' }
  }

  const autoPull = async () => {
    if (!from || !to) { setErr('Избери период.'); return }
    setBusy(true); setErr(''); setPreview(null)
    try {
      setPreview(await api.post<PayoutPreview>('/api/cod/reconcile-auto', { courier, date_from: from, date_to: to }))
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : 'Изтеглянето се провали.')
    } finally { setBusy(false) }
  }

  const discrepancies = (preview?.matched || []).filter((m) => m.discrepancy).length

  return (
    <Sheet tilt="l">
      <div style={{ display: 'flex', gap: '.6rem', alignItems: 'baseline', flexWrap: 'wrap', marginBottom: '.4rem' }}>
        <h3 style={{ fontSize: '1rem', flex: 1 }}>Изплащания от куриер</h3>
        <label className="btn-paper" style={{ cursor: 'pointer' }}>
          {busy ? 'чете…' : 'Избери файл (CSV)'}
          <input ref={inputRef} type="file" accept=".csv,text/csv,text/plain" style={{ display: 'none' }}
            aria-label="файл за изплащане от куриер"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) void onFile(f) }} />
        </label>
      </div>
      {/* auto-pull directly from the courier API (validated live at launch) */}
      <div style={{ display: 'flex', gap: '.4rem', flexWrap: 'wrap', alignItems: 'center', marginBottom: '.5rem' }}>
        <span className="font-type" style={{ fontSize: '.72rem', color: 'var(--pencil)' }}>или изтегли автоматично:</span>
        <select className="input-paper" style={{ fontSize: '.76rem' }} value={courier} onChange={(e) => setCourier(e.target.value)}>
          <option value="speedy">Speedy</option><option value="econt">Econt</option>
        </select>
        <input className="input-paper" type="date" style={{ fontSize: '.76rem' }} value={from} onChange={(e) => setFrom(e.target.value)} aria-label="от дата" />
        <input className="input-paper" type="date" style={{ fontSize: '.76rem' }} value={to} onChange={(e) => setTo(e.target.value)} aria-label="до дата" />
        <button className="btn-paper" onClick={() => void autoPull()} disabled={busy}>Изтегли</button>
      </div>
      <p style={{ color: 'var(--pencil)', fontSize: '.8rem', margin: '0 0 .6rem' }}>
        качи разчетния файл или изтегли от куриера — сверяваме по товарителница и попълваме формата долу
      </p>
      {err && <p style={{ color: 'var(--red)', fontFamily: 'var(--type)', fontSize: '.82rem' }}>Грешка: {err}</p>}

      {preview && (
        <div style={{ display: 'grid', gap: '.6rem' }}>
          <div className="font-type" style={{ fontSize: '.8rem', display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
            <span style={{ color: 'var(--ready)' }}>● {preview.matched.length} сверени</span>
            {discrepancies > 0 && <span style={{ color: 'var(--pending)' }}>● {discrepancies} с разлика</span>}
            {preview.unmatched_rows.length > 0 && <span style={{ color: 'var(--blocked)' }}>● {preview.unmatched_rows.length} без поръчка</span>}
            {preview.delivered_not_paid.length > 0 && <span style={{ color: 'var(--red)' }}>● {preview.delivered_not_paid.length} доставени, неплатени</span>}
          </div>

          {preview.matched.length > 0 && (
            <button className="btn-paper btn-red" style={{ justifySelf: 'start' }}
              onClick={() => onApply(Object.fromEntries(preview.settle_lines.map((l) => [l.order_id, l.collected])))}>
              Попълни формата с {preview.matched.length} сверени
            </button>
          )}

          {preview.unmatched_rows.length > 0 && (
            <PreviewList title="Редове без съответстваща поръчка (платено-недоставено?)"
              rows={preview.unmatched_rows.map((u) => `${u.tracking} — ${u.collected.toFixed(2)} €`)} tone="var(--blocked)" />
          )}
          {preview.delivered_not_paid.length > 0 && (
            <PreviewList title="Доставени поръчки, липсващи във файла (куриерът ни дължи)"
              rows={preview.delivered_not_paid.map((d) => `${d.order_name} — очак. ${d.expected.toFixed(2)} €`)} tone="var(--red)" />
          )}
        </div>
      )}
    </Sheet>
  )
}

function PreviewList({ title, rows, tone }: { title: string; rows: string[]; tone: string }) {
  return (
    <div>
      <div className="font-type" style={{ fontSize: '.68rem', textTransform: 'uppercase', letterSpacing: '.05em', color: tone, marginBottom: '.2rem' }}>{title}</div>
      <div style={{ display: 'grid', gap: '.1rem', maxHeight: '9rem', overflowY: 'auto' }}>
        {rows.map((r, i) => (
          <div key={i} className="font-type" style={{ fontSize: '.78rem', borderLeft: `2px solid ${tone}`, paddingLeft: '.4rem' }}>{r}</div>
        ))}
      </div>
    </div>
  )
}

// Where the COD cash physically sits, as a single part-to-whole € bar, + the payout-cadence cue.
// Because payout is twice-weekly, "since last payout" turns the abstract awaiting figure into context.
function CodPipeline({ s }: { s: CodResponse['summary'] }) {
  const segs = [
    { label: 'в обращение', amount: s.in_transit_amount, tone: 'pending' },
    { label: 'за изплащане', amount: s.awaiting_amount, tone: 'blocked' },
    { label: 'разчетено', amount: s.settled_amount, tone: 'ready' },
  ].filter((x) => x.amount > 0)
  const total = segs.reduce((a, x) => a + x.amount, 0)
  if (total <= 0) return null
  return (
    <Sheet>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '.6rem', marginBottom: '.4rem' }}>
        <h3 style={{ fontSize: '1rem', flex: 1 }}>Пари в тръбопровода</h3>
        {s.last_payout && <span className="font-type" style={{ fontSize: '.72rem', color: 'var(--pencil)' }}>последно изплащане: {s.last_payout}</span>}
      </div>
      <div role="img" aria-label={segs.map((x) => `${x.label}: ${x.amount} €`).join(', ')}
        style={{ display: 'flex', height: '1.2rem', borderRadius: 2, overflow: 'hidden', border: '1px solid var(--line)' }}>
        {segs.map((x) => <div key={x.label} title={`${x.label}: ${x.amount.toFixed(2)} €`} style={{ width: `${(x.amount / total) * 100}%`, background: `var(--${x.tone})` }} />)}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '.4rem 1rem', marginTop: '.5rem' }}>
        {segs.map((x) => (
          <span key={x.label} className="font-type" style={{ fontSize: '.74rem', display: 'inline-flex', alignItems: 'center', gap: '.3rem' }}>
            <span style={{ width: '.6rem', height: '.6rem', borderRadius: 1, background: `var(--${x.tone})` }} />
            {x.label} · {x.amount.toFixed(2)} €
          </span>
        ))}
      </div>
    </Sheet>
  )
}

// aging badge: how long COD cash has been stuck (payout is twice-weekly, so time-in-bucket = risk)
function agingBadge(o: CodOrder) {
  if (!o.updated_ts || (o.bucket !== 'awaiting' && o.bucket !== 'in_transit')) return null
  const d = Math.floor(hoursSince(o.updated_ts) / 24)
  const color = d >= 15 ? 'var(--red)' : d >= 8 ? 'var(--blocked)' : d >= 4 ? 'var(--pending)' : 'var(--pencil)'
  return <span className="font-type" style={{ color, fontSize: '.6rem', fontWeight: d >= 8 ? 700 : 400 }}>{d}д</span>
}

function CodTable({ orders }: { orders: CodOrder[] }) {
  const [filter, setFilter] = useState<string>('all')
  // chip per bucket that actually appears, using the бг label the API already sends
  const bucketLabel: Record<string, string> = {}
  const bucketCount: Record<string, number> = {}
  for (const o of orders) { bucketLabel[o.bucket] = o.bucket_bg; bucketCount[o.bucket] = (bucketCount[o.bucket] || 0) + 1 }
  const order = ['awaiting', 'in_transit', 'preship', 'settled', 'rto']
  const chips = [{ key: 'all', label: 'всички', count: orders.length },
    ...order.filter((b) => bucketCount[b]).map((b) => ({ key: b, label: bucketLabel[b], count: bucketCount[b] }))]
  const filtered = filter === 'all' ? orders : orders.filter((o) => o.bucket === filter)
  const pg = usePager(filtered, 25)

  return (
    <div style={{ display: 'grid', gap: '.3rem' }}>
      {chips.length > 2 && <div style={{ marginBottom: '.3rem' }}><FilterChips options={chips} value={filter} onChange={setFilter} /></div>}
      <div className="font-type" style={{ display: 'grid', gridTemplateColumns: '1fr 7rem 6rem 6rem 6rem', gap: '.5rem', fontSize: '.6rem', textTransform: 'uppercase', color: 'var(--red)', letterSpacing: '.06em', paddingBottom: '.2rem', borderBottom: '1px solid var(--line)' }}>
        <span>Поръчка</span><span>Статус</span><span style={{ textAlign: 'right' }}>Очаквано</span><span style={{ textAlign: 'right' }}>Събрано</span><span>Куриер</span>
      </div>
      {pg.slice.map((o) => (
        <div key={o.order_id} style={{
          display: 'grid', gridTemplateColumns: '1fr 7rem 6rem 6rem 6rem', gap: '.5rem', alignItems: 'center',
          fontSize: '.82rem', padding: '.35rem 0', borderBottom: '1px dashed var(--line)',
          borderLeft: o.discrepancy ? '3px solid var(--blocked)' : '3px solid transparent', paddingLeft: '.5rem',
        }}>
          <span className="font-type">{o.order_name}</span>
          <span className="font-type" style={{ fontSize: '.68rem', color: BUCKET_TONE[o.bucket], display: 'flex', gap: '.4rem', alignItems: 'baseline' }}>
            ● {o.bucket_bg} {agingBadge(o)}
          </span>
          <span className="font-type" style={{ textAlign: 'right' }}>{o.expected.toFixed(2)} €</span>
          <span className="font-type" style={{ textAlign: 'right', color: o.discrepancy ? 'var(--blocked)' : 'var(--pencil)' }}>
            {o.collected != null ? o.collected.toFixed(2) + ' €' : '—'}
            {o.discrepancy && <span style={{ fontSize: '.6rem', display: 'block' }}>↯ разлика</span>}
          </span>
          <span style={{ color: 'var(--pencil)', fontSize: '.75rem' }}>{o.courier}</span>
        </div>
      ))}
      <Pager {...pg} />
    </div>
  )
}

// record a courier payout for delivered orders awaiting settlement
function SettlePanel({ orders, onDone, prefill }: { orders: CodOrder[]; onDone: () => void; prefill?: Record<string, number> | null }) {
  const [ref, setRef] = useState('')
  const [date, setDate] = useState('')
  // when a payout file was imported, seed amounts from it and pre-select only the matched orders;
  // otherwise fall back to expected + all-selected (manual entry). Remounts via key on new import.
  const [amounts, setAmounts] = useState<Record<string, string>>(
    () => Object.fromEntries(orders.map((o) => [o.order_id, (prefill?.[o.order_id] ?? o.expected).toFixed(2)])))
  const [sel, setSel] = useState<Record<string, boolean>>(
    () => Object.fromEntries(orders.map((o) => [o.order_id, prefill ? (o.order_id in prefill) : true])))
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const submit = async () => {
    const lines = orders.filter((o) => sel[o.order_id])
      .map((o) => ({ order_id: o.order_id, collected: parseFloat(amounts[o.order_id] || '0') || 0 }))
    if (!lines.length) { setMsg('Избери поне една поръчка.'); return }
    setBusy(true); setMsg('')
    try {
      const r = await api.post<{ settled: number; discrepancies: number }>('/api/cod/settle',
        { courier: orders[0]?.courier || '', payout_date: date, reference: ref, lines })
      setMsg(`Разчетени ${r.settled} поръчки${r.discrepancies ? `, ${r.discrepancies} с разлика` : ''}.`)
      onDone()
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : 'Записът се провали.')
    } finally { setBusy(false) }
  }

  return (
    <Sheet tilt="r">
      <h3 style={{ fontSize: '1rem', marginBottom: '.2rem' }}>Запиши изплащане от куриер</h3>
      <p style={{ color: 'var(--pencil)', fontSize: '.8rem', margin: '0 0 .7rem' }}>
        отбележи какво е изплатил куриерът — разликите се маркират автоматично
      </p>
      <div style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap', marginBottom: '.7rem' }}>
        <input className="input-paper" style={{ width: '12rem' }} aria-label="референция на изплащане" placeholder="референция на изплащане" value={ref} onChange={(e) => setRef(e.target.value)} />
        <input className="input-paper" style={{ width: '10rem' }} type="date" aria-label="дата на изплащане" value={date} onChange={(e) => setDate(e.target.value)} />
      </div>
      <div style={{ display: 'grid', gap: '.3rem', marginBottom: '.7rem' }}>
        {orders.map((o) => (
          <div key={o.order_id} style={{ display: 'grid', gridTemplateColumns: '1.5rem 1fr 6rem 8rem', gap: '.5rem', alignItems: 'center', fontSize: '.82rem' }}>
            <input type="checkbox" checked={!!sel[o.order_id]} onChange={(e) => setSel({ ...sel, [o.order_id]: e.target.checked })} />
            <span className="font-type">{o.order_name}</span>
            <span style={{ color: 'var(--pencil)', textAlign: 'right' }}>очак. {o.expected.toFixed(2)} €</span>
            <input className="input-paper" inputMode="decimal" aria-label={`събрано за ${o.order_name}`}
              value={amounts[o.order_id] ?? ''}
              onChange={(e) => setAmounts({ ...amounts, [o.order_id]: e.target.value })} />
          </div>
        ))}
      </div>
      {msg && <div className="font-type" style={{ fontSize: '.8rem', marginBottom: '.4rem' }}>{msg}</div>}
      <button className="btn-paper btn-red" disabled={busy} onClick={() => void submit()}>
        {busy ? 'записва…' : 'Запиши изплащане'}
      </button>
    </Sheet>
  )
}
