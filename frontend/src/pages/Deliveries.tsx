import { useEffect, useRef, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { motion, AnimatePresence } from 'framer-motion'
import { api, ApiError } from '../lib/api'
import type { DeliveryDetailResp, DeliveryLineRow, DeliveryListItem } from '../lib/api'
import { useAuth } from '../lib/auth'
import { PageHeader, Loading } from '../components/PageHeader'
import { Sheet } from '../components/Paper'
import { useConfirm } from '../components/Confirm'
import { CameraScanner } from '../components/CameraScanner'
import { DeliveryImportMapper } from '../components/DeliveryImportMapper'

// Доставки — receiving deliveries. List past deliveries → process a new one (supplier + warehouse +
// Excel/CSV or a photo) → the sheet is split into already-in-catalog vs NEW → staff scan-confirm
// quantities with a handheld barcode scanner (green = matches, red = mismatch) → new products get
// added on Приемане → FINALISE sends the whole thing to Barsy as ONE delivery. (Ordering suppliers
// lives on its own Заявки page.)

const WAREHOUSES = [{ id: 1, name: 'Основен склад' }]   // one for now; the model supports more

export function Deliveries({ embedded = false }: { embedded?: boolean } = {}) {
  return (
    <div style={{ display: 'grid', gap: '1rem' }}>
      {!embedded && <PageHeader title="Доставки" subtitle="приемане на стока" />}
      <Receiving />
    </div>
  )
}

// ------------------------------------------------------------------ receiving tab ---
function Receiving() {
  const [view, setView] = useState<'list' | 'new'>('list')
  const [openId, setOpenId] = useState<number | null>(null)

  if (openId != null) return <DeliveryDetail id={openId} onBack={() => { setOpenId(null); setView('list') }} />
  if (view === 'new') return <NewDelivery onCancel={() => setView('list')} onCreated={(id) => setOpenId(id)} />
  return <DeliveryList onNew={() => setView('new')} onOpen={setOpenId} />
}

const STATUS_LABEL: Record<string, [string, string]> = {
  awaiting_products: ['чакат нови продукти', 'var(--pending)'],
  ready: ['готова за финализиране', 'var(--ready)'],
  finalised: ['финализирана', 'var(--pencil)'],
  draft: ['чернова', 'var(--pencil)'],
}

function StatusBadge({ status }: { status: string }) {
  const [label, color] = STATUS_LABEL[status] || [status, 'var(--pencil)']
  return <span className="font-type" style={{ fontSize: '.7rem', color, border: `1px solid ${color}`, borderRadius: 2, padding: '.1rem .4rem' }}>{label}</span>
}

// ------------------------------------------------------------------ list ---
function DeliveryList({ onNew, onOpen }: { onNew: () => void; onOpen: (id: number) => void }) {
  const [all, setAll] = useState<DeliveryListItem[] | null>(null)
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  useEffect(() => { api.get<{ deliveries: DeliveryListItem[] }>('/api/deliveries').then((d) => setAll(d.deliveries)).catch(() => setAll([])) }, [])
  const dateOf = (d: DeliveryListItem) => d.doc_date || (d.created_at || '').slice(0, 10)   // YYYY-MM-DD, comparable as string
  const rows = (all || []).filter((d) =>
    (!q.trim() || (d.supplier + ' ' + d.reference).toLowerCase().includes(q.trim().toLowerCase())) &&
    (!status || d.status === status) &&
    (!from || dateOf(d) >= from) && (!to || dateOf(d) <= to))
  return (
    <div style={{ display: 'grid', gap: '.8rem' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: '.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
        <div style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
          <input className="input-paper" style={{ width: '13rem' }} placeholder="търси доставчик / №…" value={q} onChange={(e) => setQ(e.target.value)} />
          <select className="input-paper" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">— всички статуси —</option>
            {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v[0]}</option>)}
          </select>
          <span className="font-type" style={{ fontSize: '.72rem', color: 'var(--pencil)' }}>период:</span>
          <input className="input-paper" type="date" value={from} onChange={(e) => setFrom(e.target.value)} title="от дата" />
          <input className="input-paper" type="date" value={to} onChange={(e) => setTo(e.target.value)} title="до дата" />
          {(from || to || q || status) && <button className="btn-paper" style={{ fontSize: '.7rem' }} onClick={() => { setQ(''); setStatus(''); setFrom(''); setTo('') }}>изчисти</button>}
        </div>
        <button className="btn-paper btn-red" onClick={onNew}>+ Обработи нова доставка</button>
      </div>
      <Sheet>
        {!all ? <Loading /> : rows.length === 0 ? (
          <p className="font-type" style={{ color: 'var(--pencil)', fontSize: '.85rem' }}>{(all.length ? 'Няма доставки за този филтър.' : 'Още няма обработени доставки.')}</p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table className="font-type" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.8rem' }}>
              <thead><tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--red)', textAlign: 'left' }}>
                <th style={{ padding: '.3rem' }}>Доставчик</th><th style={{ padding: '.3rem' }}>№</th>
                <th style={{ padding: '.3rem' }}>Дата</th><th style={{ padding: '.3rem', textAlign: 'right' }}>Артикули</th>
                <th style={{ padding: '.3rem', textAlign: 'right' }}>Нови</th><th style={{ padding: '.3rem' }}>Статус</th><th></th>
              </tr></thead>
              <tbody>
                {rows.map((d) => (
                  <tr key={d.id} style={{ borderBottom: '1px dashed var(--line)', cursor: 'pointer' }} onClick={() => onOpen(d.id)}>
                    <td style={{ padding: '.3rem', fontWeight: 700 }}>{d.supplier || '—'}</td>
                    <td style={{ padding: '.3rem', color: 'var(--pencil)' }}>{d.reference || '—'}</td>
                    <td style={{ padding: '.3rem', color: 'var(--pencil)' }}>{d.doc_date || (d.created_at || '').slice(0, 10)}</td>
                    <td style={{ padding: '.3rem', textAlign: 'right' }}>{d.total}</td>
                    <td style={{ padding: '.3rem', textAlign: 'right', color: d.new ? 'var(--pending)' : 'var(--pencil)' }}>{d.new}</td>
                    <td style={{ padding: '.3rem' }}><StatusBadge status={d.status} /></td>
                    {/* the row is click-only for the mouse; the keyboard needs a REAL control — and a
                        <tr role="button"> would break the table's semantics, so the button lives here */}
                    <td style={{ padding: '.3rem', color: 'var(--red)' }}>
                      <button type="button" onClick={(e) => { e.stopPropagation(); onOpen(d.id) }}
                        aria-label={`Отвори доставка ${d.supplier || ''} ${d.reference || ''}`.trim()}
                        style={{ background: 'none', border: 'none', padding: 0, font: 'inherit', color: 'inherit', cursor: 'pointer' }}>
                        отвори →
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Sheet>
    </div>
  )
}

// ------------------------------------------------------------------ new-delivery wizard ---
function NewDelivery({ onCancel, onCreated }: { onCancel: () => void; onCreated: (id: number) => void }) {
  const [supplier, setSupplier] = useState('')
  const [depot, setDepot] = useState(1)
  const [reference, setReference] = useState('')
  const [docDate, setDocDate] = useState('')
  const [mode, setMode] = useState<'file' | 'photo'>('file')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [ocrRows, setOcrRows] = useState<{ supplier_code: string; name: string; qty: number; price: number; barcode: string }[] | null>(null)
  const [ocrNote, setOcrNote] = useState('')
  const photoRef = useRef<HTMLInputElement>(null)

  const uploadPhoto = async (f?: File) => {
    if (!f) return
    setBusy(true); setError('')
    try {
      const form = new FormData(); form.append('file', f)
      const res = await api.upload<{ rows: typeof ocrRows; note: string }>('/api/deliveries/ocr', form)
      setOcrRows(res.rows || []); setOcrNote(res.note || '')
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Разчитането се провали.') } finally { setBusy(false) }
  }

  const createFromRows = async () => {
    if (!ocrRows) return
    setBusy(true); setError('')
    try {
      const b = await api.post<DeliveryDetailResp>('/api/deliveries/from-rows', {
        supplier, supplier_id: null, depot_id: depot, reference, doc_date: docDate, source: 'photo', rows: ocrRows,
      })
      onCreated(b.id)
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Създаването се провали.') } finally { setBusy(false) }
  }

  return (
    <div style={{ display: 'grid', gap: '.9rem' }}>
      <button className="btn-paper" onClick={onCancel} style={{ justifySelf: 'start' }}>← Назад</button>
      <Sheet clip="left" tilt="l">
        <div className="eyebrow">Нова доставка</div>
        <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', marginTop: '.6rem' }}>
          <Field label="Доставчик"><input className="input-paper" value={supplier} onChange={(e) => setSupplier(e.target.value)} placeholder="име на доставчика" style={{ minWidth: '13rem' }} /></Field>
          <Field label="Склад">
            <select className="input-paper" value={depot} onChange={(e) => setDepot(Number(e.target.value))}>
              {WAREHOUSES.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
            </select>
          </Field>
          <Field label="№ фактура"><input className="input-paper" value={reference} onChange={(e) => setReference(e.target.value)} placeholder="напр. 0000012345" /></Field>
          <Field label="Дата"><input className="input-paper" type="date" value={docDate} onChange={(e) => setDocDate(e.target.value)} /></Field>
        </div>

        <div style={{ display: 'flex', gap: '.4rem', margin: '.9rem 0 .5rem' }}>
          {([['file', '📄 Excel / CSV файл'], ['photo', '📷 Нямам файл, само хартия']] as ['file' | 'photo', string][]).map(([k, l]) => (
            <button key={k} className="font-type" onClick={() => { setMode(k); setOcrRows(null) }} style={{
              padding: '.35rem .8rem', fontSize: '.76rem', cursor: 'pointer', borderRadius: 2,
              border: '1px solid var(--line)', background: mode === k ? 'var(--paper)' : 'var(--paper2)',
              color: mode === k ? 'var(--ink)' : 'var(--pencil)', fontWeight: mode === k ? 700 : 400,
            }}>{l}</button>
          ))}
        </div>

        {mode === 'file' ? (
          <DeliveryImportMapper
            supplier={supplier} depot={depot} reference={reference} docDate={docDate}
            onCreated={onCreated}
          />
        ) : (
          <div style={{ display: 'grid', gap: '.5rem' }}>
            <div style={{ display: 'flex', gap: '.6rem', alignItems: 'center' }}>
              <input ref={photoRef} type="file" accept="image/*" capture="environment" style={{ display: 'none' }} onChange={(e) => void uploadPhoto(e.target.files?.[0])} />
              <button className="btn-paper btn-red" onClick={() => photoRef.current?.click()} disabled={busy}>{busy ? 'разчита…' : 'Снимай / качи снимка'}</button>
              <span className="font-type" style={{ fontSize: '.74rem', color: 'var(--pencil)' }}>снимка на хартиената таблица — разчита се автоматично</span>
            </div>
            {ocrRows && <OcrEditor rows={ocrRows} note={ocrNote} onChange={setOcrRows} onCreate={() => void createFromRows()} busy={busy} />}
          </div>
        )}
        {error && <div style={{ color: 'var(--red)', fontFamily: 'var(--type)', fontSize: '.82rem', marginTop: '.6rem' }}>{error}</div>}
      </Sheet>
    </div>
  )
}

// editable OCR draft — staff correct before creating (photo OCR is best-effort, never trusted as-is)
function OcrEditor({ rows, note, onChange, onCreate, busy }: {
  rows: { supplier_code: string; name: string; qty: number; price: number; barcode: string }[]
  note: string; onChange: (r: typeof rows) => void; onCreate: () => void; busy: boolean
}) {
  const upd = (i: number, k: string, v: string) => {
    const next = rows.map((r, j) => j === i ? { ...r, [k]: k === 'qty' || k === 'price' ? Number(v) || 0 : v } : r)
    onChange(next)
  }
  return (
    <div style={{ display: 'grid', gap: '.5rem', marginTop: '.4rem' }}>
      <p className="font-type" style={{ fontSize: '.74rem', color: 'var(--pending)' }}>⚠ {note}</p>
      <div style={{ overflowX: 'auto' }}>
        <table className="font-type" style={{ width: '100%', fontSize: '.76rem', borderCollapse: 'collapse' }}>
          <thead><tr style={{ color: 'var(--red)', textAlign: 'left' }}><th>Код</th><th>Име</th><th>К-во</th><th>Цена</th></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={i}>
                <td><input className="input-paper" style={{ width: '6rem', padding: '.2rem .3rem' }} value={r.supplier_code} onChange={(e) => upd(i, 'supplier_code', e.target.value)} /></td>
                <td><input className="input-paper" style={{ width: '16rem', padding: '.2rem .3rem' }} value={r.name} onChange={(e) => upd(i, 'name', e.target.value)} /></td>
                <td><input className="input-paper" style={{ width: '4rem', padding: '.2rem .3rem' }} value={r.qty} onChange={(e) => upd(i, 'qty', e.target.value)} /></td>
                <td><input className="input-paper" style={{ width: '5rem', padding: '.2rem .3rem' }} value={r.price} onChange={(e) => upd(i, 'price', e.target.value)} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <button className="btn-paper btn-red" onClick={onCreate} disabled={busy || rows.length === 0} style={{ justifySelf: 'start' }}>
        {busy ? 'създава…' : 'Създай доставка от таблицата'}
      </button>
    </div>
  )
}

// ------------------------------------------------------------------ detail + scanning ---
function DeliveryDetail({ id, onBack }: { id: number; onBack: () => void }) {
  const { me } = useAuth()
  const [b, setB] = useState<DeliveryDetailResp | null>(null)
  const [error, setError] = useState('')
  const [scanMsg, setScanMsg] = useState('')
  const [unknown, setUnknown] = useState<{ barcode: string; lines: DeliveryLineRow[] } | null>(null)
  const [finalising, setFinalising] = useState(false)
  const [result, setResult] = useState<{ dry_run: boolean; storeload_id?: number | null; warnings: string[] } | null>(null)
  const bufferRef = useRef('')
  const [showCam, setShowCam] = useState(false)
  const unknownQueueRef = useRef<string[]>([])
  const canFinalise = me?.role === 'owner' || me?.role === 'manager'

  const load = useCallback(() => api.get<DeliveryDetailResp>(`/api/deliveries/${id}`).then((d) => { setB(d); return d }).catch(() => { setError('Грешка при зареждане.'); return undefined }), [id])
  useEffect(() => { void load() }, [load])

  const doScan = useCallback(async (barcode: string) => {
    if (!barcode.trim()) return
    try {
      const r = await api.post<{ matched: boolean; unknown?: boolean; line?: DeliveryLineRow; new_lines?: DeliveryLineRow[] }>(`/api/deliveries/${id}/scan`, { barcode })
      if (r.unknown) { setUnknown({ barcode, lines: r.new_lines || [] }); setScanMsg('') }
      else if (r.line) { setScanMsg(`✓ ${r.line.name}: ${r.line.scanned_qty}/${r.line.qty}`); await load() }
    } catch (e) { setScanMsg(e instanceof ApiError ? e.message : 'Скан грешка') }
  }, [id, load])

  // always-listening scanner: HID scanners "type" the whole code in a fast burst + Enter. Buffer
  // keystrokes globally and fire on Enter. Guards: (a) ignore while a real input/textarea is focused
  // (manual typing), (b) ignore keys held with Ctrl/Meta/Alt (shortcuts like Ctrl+C yield key "c"),
  // (c) reset the buffer if >120 ms elapsed since the last char — that inter-keystroke gap is the
  // standard scanner-vs-human discriminator, so idle human keypresses can't accumulate a fake code.
  const lastKeyRef = useRef(0)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = document.activeElement as HTMLElement | null
      if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)) return
      if (e.ctrlKey || e.metaKey || e.altKey) return
      const now = e.timeStamp || 0
      if (e.key === 'Enter') { const code = bufferRef.current; bufferRef.current = ''; if (code) void doScan(code); return }
      if (e.key.length !== 1) return
      if (now - lastKeyRef.current > 120) bufferRef.current = ''   // too slow → not a scan; restart
      lastKeyRef.current = now
      bufferRef.current += e.key
    }
    const onBlur = () => { bufferRef.current = '' }
    window.addEventListener('keydown', onKey)
    window.addEventListener('blur', onBlur)
    return () => { window.removeEventListener('keydown', onKey); window.removeEventListener('blur', onBlur) }
  }, [doScan])

  const confirm = useConfirm()
  // a camera batch can surface several unknown codes at once — the dialog walks them one by one
  const advanceUnknown = useCallback((lines?: DeliveryLineRow[]) => {
    const code = unknownQueueRef.current.shift()
    if (!code) { setUnknown(null); return }
    setUnknown({ barcode: code, lines: (lines ?? b?.lines ?? []).filter((l) => l.is_new) })
  }, [b])

  const attach = async (lineId: number) => {
    if (!unknown) return
    try {
      await api.post(`/api/deliveries/${id}/lines/${lineId}/attach-barcode`, { barcode: unknown.barcode })
      const fresh = await load()
      advanceUnknown(fresh?.lines)
    } catch (e) { setScanMsg(e instanceof ApiError ? e.message : 'Грешка') }
  }

  // one atomic commit from the camera scanner; unknown codes queue through the attach
  // dialog. Returns the result string so the scanner card can show it right where the
  // worker is looking — the first fullscreen version put every reaction BEHIND the camera,
  // which read as "Добави does nothing" in the owner's field test.
  const applyBatch = useCallback(async (counts: Record<string, number>): Promise<string> => {
    try {
      const r = await api.post<{ applied: { barcode: string }[]; unknown: string[] }>(`/api/deliveries/${id}/scan-batch`, { counts })
      const fresh = await load()
      const m = `✓ ${r.applied.length} кода добавени` + (r.unknown.length ? ` · ${r.unknown.length} непознати` : '')
      setScanMsg(m)
      if (r.unknown.length) {
        unknownQueueRef.current = r.unknown.slice(1)
        setUnknown({ barcode: r.unknown[0], lines: (fresh?.lines ?? []).filter((l) => l.is_new) })
      }
      return m
    } catch (e) {
      const m = e instanceof ApiError ? e.message : 'Скан грешка — няма връзка'
      setScanMsg(m)
      return '⚠ ' + m
    }
  }, [id, load])

  const finalise = async (force = false) => {
    if (!(await confirm({ confirmLabel: 'Финализирай', message: 'Финализиране на доставката и изпращане към Barsy?' }))) return
    setFinalising(true); setError(''); setResult(null)
    try {
      const r = await api.post<{ dry_run: boolean; storeload_id?: number | null; warnings: string[] }>(`/api/deliveries/${id}/finalise`, { confirm: true, force })
      setResult(r); await load()
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Финализирането се провали.') } finally { setFinalising(false) }
  }

  if (!b) return <Loading />
  const existing = b.lines.filter((l) => !l.is_new)
  const news = b.lines.filter((l) => l.is_new)

  return (
    <div style={{ display: 'grid', gap: '.9rem' }}>
      <button className="btn-paper" onClick={onBack} style={{ justifySelf: 'start' }}>← Всички доставки</button>

      <Sheet>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '1rem', flexWrap: 'wrap' }}>
          <div>
            <h2 style={{ fontSize: '1.1rem' }}>{b.supplier || 'Доставка'} <StatusBadge status={b.status} /></h2>
            <span className="font-type" style={{ fontSize: '.76rem', color: 'var(--pencil)' }}>
              № {b.reference || '—'} · {b.doc_date || (b.created_at || '').slice(0, 10)} · {b.total} артикула ({b.existing} заведени, {b.new} нови)
            </span>
          </div>
          <div className="font-type" style={{ fontSize: '.74rem', color: 'var(--ready)', textAlign: 'right' }}>
            📷 Сканирай с четец — редовете се потвърждават автоматично<br />
            <span style={{ color: 'var(--pencil)' }}>{b.confirmed} потвърдени · {b.mismatched} с разлика</span>
            {b.status !== 'finalised' && <div style={{ marginTop: '.45rem' }}><button className="btn-paper btn-red" onClick={() => setShowCam(true)}>📷 Сканирай с камера</button></div>}
          </div>
        </div>
        {scanMsg && <div className="font-type" style={{ marginTop: '.5rem', fontSize: '.82rem', color: 'var(--ink)' }}>{scanMsg}</div>}
      </Sheet>

      {showCam && <CameraScanner onClose={() => setShowCam(false)} onCommit={applyBatch} />}

      {b.status !== 'finalised' && news.length > 0 && (
        <div className="font-type" style={{ background: 'var(--sticky)', color: '#2b2607', padding: '.7rem .9rem', borderRadius: 3, fontSize: '.84rem' }}>
          Има {news.filter((l) => l.article_id == null).length} нови продукта, които не са добавени.{' '}
          <Link to="/intake" style={{ color: 'var(--red-ink)', fontWeight: 700 }}>Отиди на Нов продукт →</Link>
        </div>
      )}

      {existing.length > 0 && (
        <Sheet>
          <h3 style={{ fontSize: '.95rem', marginBottom: '.4rem' }}>Заведени продукти ({existing.length})</h3>
          <LineTable lines={existing} />
        </Sheet>
      )}
      {news.length > 0 && (
        <Sheet>
          <h3 style={{ fontSize: '.95rem', marginBottom: '.4rem' }}>Нови продукти ({news.length})</h3>
          <LineTable lines={news} isNew />
        </Sheet>
      )}

      <Sheet clip="right">
        <div style={{ display: 'flex', gap: '.7rem', alignItems: 'center', flexWrap: 'wrap' }}>
          {b.status === 'finalised' ? (
            <span className="font-type" style={{ color: 'var(--ready)', fontWeight: 700 }}>✓ Финализирана · доставка #{b.storeload_id}</span>
          ) : (
            <>
              <button className="btn-paper btn-red" onClick={() => void finalise(false)} disabled={finalising || !canFinalise || b.new_pending > 0}>
                {finalising ? 'финализира…' : 'Финализирай доставката'}
              </button>
              {b.mismatched > 0 && canFinalise && b.new_pending === 0 &&
                <button className="btn-paper" onClick={() => void finalise(true)} disabled={finalising}>Финализирай въпреки разликите</button>}
              {!canFinalise && <span className="font-type" style={{ fontSize: '.74rem', color: 'var(--pencil)' }}>само собственик/мениджър финализира</span>}
              {b.new_pending > 0 && <span className="font-type" style={{ fontSize: '.74rem', color: 'var(--pending)' }}>първо завърши новите продукти в „Нов продукт“</span>}
            </>
          )}
        </div>
        {result && (
          <div className="font-type" style={{ marginTop: '.6rem', fontSize: '.8rem', color: result.dry_run ? 'var(--pending)' : 'var(--ready)' }}>
            {result.dry_run ? 'Проба (нищо не е записано в Barsy)' : `Изпратено · доставка #${result.storeload_id}`}
            {result.warnings.map((w, i) => <div key={i} style={{ color: 'var(--pending)' }}>⚠ {w}</div>)}
          </div>
        )}
        {error && <div style={{ color: 'var(--red)', fontFamily: 'var(--type)', fontSize: '.82rem', marginTop: '.5rem' }}>{error}</div>}
      </Sheet>

      {/* unknown-barcode → attach to a new product */}
      <AnimatePresence>
        {unknown && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.4)', display: 'grid', placeItems: 'center', zIndex: 50 }}
            onClick={() => { unknownQueueRef.current = []; setUnknown(null) }}>
            <div className="sheet" style={{ padding: '1.1rem', maxWidth: '30rem', width: '90%' }} onClick={(e) => e.stopPropagation()}>
              <h3 style={{ fontSize: '1rem' }}>Непознат баркод</h3>
              <p className="font-type" style={{ fontSize: '.82rem', color: 'var(--pencil)', margin: '.4rem 0' }}>
                Баркод <b>{unknown.barcode}</b> не е в системата. Да го прикача ли към нов продукт?
              </p>
              <div style={{ display: 'grid', gap: '.35rem', maxHeight: '16rem', overflowY: 'auto' }}>
                {unknown.lines.length === 0 && <span className="font-type" style={{ fontSize: '.8rem', color: 'var(--pencil)' }}>Няма нови продукти в тази доставка.</span>}
                {unknown.lines.map((l) => (
                  <button key={l.id} className="btn-paper" style={{ textAlign: 'left' }} onClick={() => void attach(l.id)}>
                    {l.name} <span style={{ color: 'var(--pencil)' }}>· код {l.supplier_code || '—'}</span>
                  </button>
                ))}
              </div>
              <button className="btn-paper" style={{ marginTop: '.7rem' }} onClick={() => advanceUnknown()}>Не прикачвай</button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

function LineTable({ lines, isNew = false }: { lines: DeliveryLineRow[]; isNew?: boolean }) {
  const rowColor = (l: DeliveryLineRow) =>
    l.status === 'confirmed' ? 'rgba(47,125,79,.14)' : l.status === 'mismatch' ? 'rgba(177,47,38,.14)' : 'transparent'
  return (
    <div style={{ overflowX: 'auto' }}>
      <table className="font-type" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.78rem' }}>
        <thead><tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--red)', textAlign: 'left' }}>
          <th style={{ padding: '.25rem .35rem' }}>Продукт</th><th style={{ padding: '.25rem .35rem' }}>Код</th>
          <th style={{ padding: '.25rem .35rem' }}>Баркод</th><th style={{ padding: '.25rem .35rem', textAlign: 'right' }}>Скан / Док.</th>
          <th style={{ padding: '.25rem .35rem', textAlign: 'right' }}>Цена</th>
          {isNew && <th style={{ padding: '.25rem .35rem' }}>Barsy</th>}
        </tr></thead>
        <tbody>
          {lines.map((l) => (
            <tr key={l.id} style={{ borderBottom: '1px dashed var(--line)', background: rowColor(l) }}>
              <td style={{ padding: '.3rem .35rem' }}>{l.name}</td>
              <td style={{ padding: '.3rem .35rem', color: 'var(--pencil)' }}>{l.supplier_code || l.match_code || '—'}</td>
              <td style={{ padding: '.3rem .35rem', color: 'var(--pencil)', fontVariantNumeric: 'tabular-nums' }}>{l.barcode || '—'}</td>
              <td style={{ padding: '.3rem .35rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums', fontWeight: 700,
                           color: l.status === 'confirmed' ? 'var(--ready)' : l.status === 'mismatch' ? 'var(--blocked)' : 'var(--ink)' }}>
                {l.scanned_qty} / {l.qty}
              </td>
              <td style={{ padding: '.3rem .35rem', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{l.price.toFixed(2)} €</td>
              {isNew && <td style={{ padding: '.3rem .35rem' }}>{l.article_id ? <span style={{ color: 'var(--ready)' }}>#{l.article_id}</span> : <span style={{ color: 'var(--pending)' }}>чака</span>}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'grid', gap: '.2rem' }}>
      <span className="font-type" style={{ fontSize: '.62rem', color: 'var(--red)', textTransform: 'uppercase', letterSpacing: '.06em' }}>{label}</span>
      {children}
    </label>
  )
}
