import { useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { api, ApiError } from '../lib/api'
import type {
  DeliveryParseResponse, DeliveryRow, DeliveryCommitResponse, IntakeDraft, IntakeAttribute,
} from '../lib/api'
import { useAuth } from '../lib/auth'
import { Sheet } from '../components/Paper'
import { Loading } from '../components/PageHeader'

// Delivery-based intake. A supplier's Excel (their code, name, qty, delivery price) is parsed and
// split into ALREADY-IN-CATALOG (just received) vs NEW (must be enriched: barcode + images →
// category/name/description). Staff mark each new product ready; the WHOLE delivery is then sent to
// Barsy as ONE storeload. Until DELIVERY_BARSY_LIVE is on, commit dry-runs (shows the payload).

const UNITS = ['бр', 'л', 'кг', 'м', 'м2', 'компл']

// per-new-product working copy (the AI draft in 'pencil' + operational fields in 'pen')
interface NewEdit {
  code: string
  barcode: string
  sale_price: string
  unit: string
  name: string
  category: string
  description: string
  attributes: IntakeAttribute[]
  images: { name: string; url: string }[]
  enriching: boolean
  ready: boolean
  error: string
}

const blankEdit = (r: DeliveryRow): NewEdit => ({
  code: r.supplier_code, barcode: '', sale_price: '', unit: 'бр',
  name: r.name, category: '', description: '', attributes: [],
  images: [], enriching: false, ready: false, error: '',
})

export function IntakeDelivery({ categories }: { categories: string[] }) {
  const { me } = useAuth()
  const fileRef = useRef<HTMLInputElement>(null)
  const [parsing, setParsing] = useState(false)
  const [parsed, setParsed] = useState<DeliveryParseResponse | null>(null)
  const [error, setError] = useState('')
  const [filename, setFilename] = useState('')
  // delivery header
  const [supplier, setSupplier] = useState('')
  const [reference, setReference] = useState('')
  const [date, setDate] = useState('')
  // per-new-product edits, keyed by source row number
  const [edits, setEdits] = useState<Record<number, NewEdit>>({})
  const [committing, setCommitting] = useState(false)
  const [result, setResult] = useState<DeliveryCommitResponse | null>(null)

  const canCommit = me?.role === 'owner' || me?.role === 'manager'

  const onFile = async (f: File | undefined) => {
    if (!f) return
    setParsing(true); setError(''); setParsed(null); setResult(null); setEdits({})
    setFilename(f.name)
    try {
      const form = new FormData()
      form.append('file', f)
      if (supplier.trim()) form.append('supplier', supplier.trim())   // sharpens the code match
      const p = await api.upload<DeliveryParseResponse>('/api/intake/delivery/parse', form)
      setParsed(p)
      // seed an edit record for every NEW row so staff can enrich it
      const seed: Record<number, NewEdit> = {}
      for (const r of p.rows) if (r.is_new) seed[r.row] = blankEdit(r)
      setEdits(seed)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Файлът не можа да се обработи.')
    } finally { setParsing(false) }
  }

  const patch = (row: number, upd: Partial<NewEdit>) =>
    setEdits((m) => ({ ...m, [row]: { ...m[row], ...upd } }))

  const enrich = async (r: DeliveryRow) => {
    patch(r.row, { enriching: true, error: '' })
    try {
      // reuses the free-quota AI draft (name→category/attributes/description). Image + barcode
      // (GS1 / reverse-image) enrichment integrates here once the pipeline is wired.
      const d = await api.post<IntakeDraft>('/api/intake/enrich', { name: r.name })
      patch(r.row, {
        enriching: false, name: d.name, category: d.category,
        description: d.description, attributes: d.attributes,
      })
    } catch (e) {
      patch(r.row, { enriching: false, error: e instanceof ApiError ? e.message : 'Обогатяването се провали.' })
    }
  }

  const addImages = (row: number, files: FileList | null) => {
    if (!files?.length) return
    const imgs = Array.from(files).map((f) => ({ name: f.name, url: URL.createObjectURL(f) }))
    patch(row, { images: [...(edits[row]?.images || []), ...imgs] })
  }

  const commit = async () => {
    if (!parsed) return
    if (!window.confirm('Приемане на цялата доставка в Barsy като една доставка?')) return
    setCommitting(true); setError(''); setResult(null)
    try {
      const lines = parsed.rows.map((r) => {
        if (r.is_new) {
          const e = edits[r.row]
          return {
            is_new: true, supplier_code: r.supplier_code, name: e.name, qty: r.qty,
            delivery_price: r.delivery_price, code: e.code, barcode: e.barcode,
            sale_price: e.sale_price, unit: e.unit, category: e.category,
            description: e.description, attributes: e.attributes,
          }
        }
        return {
          is_new: false, supplier_code: r.supplier_code, name: r.name, qty: r.qty,
          delivery_price: r.delivery_price, article_id: null,   // resolved server-side at go-live
        }
      })
      const res = await api.post<DeliveryCommitResponse>('/api/intake/delivery/commit', {
        supplier, reference, date, confirm: true, lines,
      })
      setResult(res)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Приемането се провали.')
    } finally { setCommitting(false) }
  }

  const newRows = parsed?.rows.filter((r) => r.is_new) ?? []
  const existingRows = parsed?.rows.filter((r) => !r.is_new) ?? []
  const allReady = newRows.length > 0 && newRows.every((r) => edits[r.row]?.ready)

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      {/* upload */}
      <Sheet clip="left" tilt="l">
        <div className="eyebrow">Доставка от доставчик</div>
        <p className="font-hand" style={{ color: 'var(--pencil)', fontSize: '.9rem', margin: '.4rem 0 .7rem' }}>
          ✎ Качи Excel-а на доставката. Ще отделя новите продукти от вече заведените и ще ги приема
          заедно като една доставка в Barsy.
        </p>
        <div style={{ display: 'flex', gap: '.6rem', alignItems: 'flex-end', flexWrap: 'wrap' }}>
          <HdrField label="Доставчик (за по-точно съвпадение)">
            <input className="input-paper" value={supplier} onChange={(e) => setSupplier(e.target.value)}
              placeholder="име на доставчика" style={{ minWidth: '14rem' }} />
          </HdrField>
          <input ref={fileRef} type="file" accept=".xlsx,.xlsm" style={{ display: 'none' }}
            onChange={(e) => void onFile(e.target.files?.[0])} />
          <button className="btn-paper btn-red" onClick={() => fileRef.current?.click()} disabled={parsing}>
            {parsing ? 'чете…' : 'Качи Excel'}
          </button>
          {filename && <span className="font-type" style={{ fontSize: '.8rem', color: 'var(--pencil)' }}>{filename}</span>}
        </div>
        {error && <div style={{ color: 'var(--red)', fontFamily: 'var(--type)', fontSize: '.82rem', marginTop: '.5rem' }}>{error}</div>}
      </Sheet>

      {parsing && <Loading />}

      {parsed && (
        <>
          {/* delivery header + summary */}
          <Sheet>
            <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', alignItems: 'flex-end' }}>
              <HdrField label="Доставчик"><input className="input-paper" value={supplier} onChange={(e) => setSupplier(e.target.value)} placeholder="име на доставчика" /></HdrField>
              <HdrField label="№ фактура / доставка"><input className="input-paper" value={reference} onChange={(e) => setReference(e.target.value)} placeholder="напр. 0000012345" /></HdrField>
              <HdrField label="Дата"><input className="input-paper" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></HdrField>
            </div>
            <div style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap', marginTop: '.8rem' }}>
              <Chip label="редове" value={parsed.summary.total} />
              <Chip label="нови" value={parsed.summary.new} tone="pending" />
              <Chip label="заведени" value={parsed.summary.existing} tone="ready" />
              <Chip label="общо к-во" value={parsed.summary.total_qty} />
            </div>
            {parsed.positional && (
              <p className="font-type" style={{ fontSize: '.72rem', color: 'var(--pending)', marginTop: '.5rem' }}>
                ⚠ Няма разпознат заглавен ред — колоните са приети по ред: код · име · количество · цена.
              </p>
            )}
          </Sheet>

          {/* NEW products — the work */}
          {newRows.length > 0 && (
            <div style={{ display: 'grid', gap: '.7rem' }}>
              <h3 style={{ fontSize: '1rem' }}>Нови продукти ({newRows.length})</h3>
              {newRows.map((r) => (
                <NewProductCard key={r.row} row={r} edit={edits[r.row]} categories={categories}
                  onEnrich={() => void enrich(r)} onPatch={(u) => patch(r.row, u)}
                  onImages={(fl) => addImages(r.row, fl)} units={UNITS} />
              ))}
            </div>
          )}

          {/* EXISTING — received as-is */}
          {existingRows.length > 0 && (
            <Sheet>
              <h3 style={{ fontSize: '1rem', marginBottom: '.4rem' }}>Вече заведени ({existingRows.length})</h3>
              <p className="font-type" style={{ fontSize: '.72rem', color: 'var(--pencil)', marginBottom: '.5rem' }}>
                Само се приемат по количество — без промяна на продукта.
              </p>
              <div style={{ overflowX: 'auto' }}>
                <table className="font-type" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.78rem' }}>
                  <thead><tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--red)', textAlign: 'left' }}>
                    <th style={{ padding: '.2rem .3rem' }}>Име</th><th style={{ padding: '.2rem .3rem' }}>Код (доставчик)</th>
                    <th style={{ padding: '.2rem .3rem' }}>Съвпадение</th><th style={{ padding: '.2rem .3rem', textAlign: 'right' }}>К-во</th>
                    <th style={{ padding: '.2rem .3rem', textAlign: 'right' }}>Дост. цена</th>
                  </tr></thead>
                  <tbody>
                    {existingRows.map((r) => (
                      <tr key={r.row} style={{ borderBottom: '1px dashed var(--line)' }}>
                        <td style={{ padding: '.25rem .3rem' }}>{r.name}</td>
                        <td style={{ padding: '.25rem .3rem', color: 'var(--pencil)' }}>{r.supplier_code || '—'}</td>
                        <td style={{ padding: '.25rem .3rem', color: r.match_confidence === 'high' ? 'var(--ready)' : 'var(--pending)' }}>
                          {r.match_method === 'code' ? 'по код' : 'по име'} · {r.match_code}
                        </td>
                        <td style={{ padding: '.25rem .3rem', textAlign: 'right' }}>{r.qty}</td>
                        <td style={{ padding: '.25rem .3rem', textAlign: 'right' }}>{r.delivery_price.toFixed(2)} €</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Sheet>
          )}

          {/* commit */}
          <Sheet clip="right">
            <div style={{ display: 'flex', gap: '.7rem', alignItems: 'center', flexWrap: 'wrap' }}>
              <button className="btn-paper btn-red" onClick={() => void commit()}
                disabled={committing || !canCommit || (newRows.length > 0 && !allReady)}>
                {committing ? 'приема…' : 'Приеми доставката в Barsy'}
              </button>
              {!canCommit && <span className="font-type" style={{ fontSize: '.76rem', color: 'var(--pencil)' }}>само собственик/мениджър приема доставки</span>}
              {newRows.length > 0 && !allReady && canCommit &&
                <span className="font-type" style={{ fontSize: '.76rem', color: 'var(--pending)' }}>маркирай всички нови продукти като готови (✓)</span>}
            </div>
            {result && <CommitResult res={result} />}
            {error && <div style={{ color: 'var(--red)', fontFamily: 'var(--type)', fontSize: '.82rem', marginTop: '.5rem' }}>{error}</div>}
          </Sheet>
        </>
      )}
    </div>
  )
}

// ------------------------------------------------------------------ per-new-product card ---
function NewProductCard({
  row, edit, categories, onEnrich, onPatch, onImages, units,
}: {
  row: DeliveryRow; edit: NewEdit; categories: string[]
  onEnrich: () => void; onPatch: (u: Partial<NewEdit>) => void
  onImages: (fl: FileList | null) => void; units: string[]
}) {
  if (!edit) return null
  const penciled = !!edit.category || !!edit.description
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
      style={{
        border: `1px solid ${edit.ready ? 'var(--ready)' : 'var(--line)'}`, borderRadius: 3,
        padding: '.8rem', background: 'var(--paper2)', display: 'grid', gap: '.6rem',
      }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '.5rem' }}>
        <span className="font-type" style={{ fontWeight: 700, fontSize: '.9rem' }}>
          ред {row.row} · {row.qty} бр · {row.delivery_price.toFixed(2)} € дост.
        </span>
        {edit.ready
          ? <span className="font-type" style={{ color: 'var(--ready)', fontSize: '.8rem' }}>✓ готов</span>
          : <button className="btn-paper" onClick={onEnrich} disabled={edit.enriching}>
              {edit.enriching ? 'обогатява…' : '✨ Обогати'}
            </button>}
      </div>

      {/* barcode + images first (feed enrichment) */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(9rem, 1fr))', gap: '.5rem' }}>
        <HdrField label="Баркод"><input className="input-paper" value={edit.barcode} onChange={(e) => onPatch({ barcode: e.target.value })} placeholder="EAN/GS1" /></HdrField>
        <HdrField label="Наш код"><input className="input-paper" value={edit.code} onChange={(e) => onPatch({ code: e.target.value })} /></HdrField>
        <HdrField label="Цена продажба (€)"><input className="input-paper" inputMode="decimal" value={edit.sale_price} onChange={(e) => onPatch({ sale_price: e.target.value })} /></HdrField>
        <HdrField label="Мярка">
          <select className="input-paper" value={edit.unit} onChange={(e) => onPatch({ unit: e.target.value })}>
            {units.map((u) => <option key={u} value={u}>{u}</option>)}
          </select>
        </HdrField>
      </div>

      {/* images */}
      <div>
        <label className="font-type" style={{ fontSize: '.62rem', color: 'var(--red)', textTransform: 'uppercase', letterSpacing: '.06em' }}>Снимки</label>
        <div style={{ display: 'flex', gap: '.4rem', flexWrap: 'wrap', alignItems: 'center', marginTop: '.3rem' }}>
          {edit.images.map((im, i) => (
            <img key={i} src={im.url} alt={im.name} style={{ width: 48, height: 48, objectFit: 'cover', borderRadius: 3, border: '1px solid var(--line)' }} />
          ))}
          <label className="btn-paper" style={{ cursor: 'pointer', fontSize: '.72rem' }}>
            + снимки
            <input type="file" accept="image/*" multiple style={{ display: 'none' }} onChange={(e) => onImages(e.target.files)} />
          </label>
        </div>
      </div>

      {/* AI suggestions (pencil) — editable */}
      <div style={{ display: 'grid', gap: '.5rem', opacity: penciled ? 1 : 0.6 }}>
        <HdrField label="Търговско име">
          <input className="input-paper" value={edit.name} onChange={(e) => onPatch({ name: e.target.value })} />
        </HdrField>
        <HdrField label="Категория">
          <select className="input-paper" value={edit.category} onChange={(e) => onPatch({ category: e.target.value })}>
            <option value="">— избери —</option>
            {[edit.category, ...categories.filter((c) => c !== edit.category)].filter(Boolean).map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </HdrField>
        <HdrField label="Описание">
          <textarea className="input-paper" rows={3} value={edit.description}
            style={{ fontFamily: penciled ? 'var(--serif)' : 'var(--hand)' }}
            onChange={(e) => onPatch({ description: e.target.value })} />
        </HdrField>
        {edit.attributes.length > 0 && (
          <div style={{ fontSize: '.76rem', color: 'var(--pencil)' }}>
            {edit.attributes.map((a, i) => <span key={i} style={{ marginRight: '.6rem' }}><b>{a.key}:</b> {a.value}</span>)}
          </div>
        )}
      </div>

      {edit.error && <div style={{ color: 'var(--red)', fontFamily: 'var(--type)', fontSize: '.8rem' }}>{edit.error}</div>}

      <div style={{ display: 'flex', gap: '.5rem' }}>
        <button className="btn-paper" onClick={() => onPatch({ ready: !edit.ready })}
          style={edit.ready ? { borderColor: 'var(--ready)', color: 'var(--ready)' } : undefined}
          disabled={!edit.code.trim()}>
          {edit.ready ? '✓ готов (отмени)' : 'Маркирай готов'}
        </button>
        {!edit.code.trim() && <span className="font-type" style={{ fontSize: '.72rem', color: 'var(--pending)', alignSelf: 'center' }}>нужен е наш код</span>}
      </div>
    </motion.div>
  )
}

// ------------------------------------------------------------------ commit result ---
function CommitResult({ res }: { res: DeliveryCommitResponse }) {
  return (
    <div style={{ marginTop: '.7rem', border: '1px solid var(--line)', borderRadius: 3, padding: '.7rem', background: 'var(--paper)' }}>
      <div className="font-type" style={{ fontWeight: 700, color: res.dry_run ? 'var(--pending)' : 'var(--ready)' }}>
        {res.dry_run ? 'Проба (нищо не е записано)' : `Прието · доставка #${res.storeload_id}`}
      </div>
      {res.warnings.map((w, i) => (
        <div key={i} className="font-type" style={{ fontSize: '.76rem', color: 'var(--pending)', marginTop: '.3rem' }}>⚠ {w}</div>
      ))}
      {res.dry_run && (
        <details style={{ marginTop: '.5rem' }}>
          <summary className="font-type" style={{ fontSize: '.76rem', color: 'var(--pencil)', cursor: 'pointer' }}>виж данните за Barsy</summary>
          <pre style={{ fontSize: '.68rem', overflowX: 'auto', background: 'var(--paper2)', padding: '.5rem', borderRadius: 2, marginTop: '.3rem' }}>
            {JSON.stringify(res.storeload_payload, null, 2)}
          </pre>
        </details>
      )}
    </div>
  )
}

// ------------------------------------------------------------------ small bits ---
function HdrField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'grid', gap: '.2rem' }}>
      <span className="font-type" style={{ fontSize: '.62rem', color: 'var(--red)', textTransform: 'uppercase', letterSpacing: '.06em' }}>{label}</span>
      {children}
    </label>
  )
}

function Chip({ label, value, tone }: { label: string; value: number; tone?: 'ready' | 'pending' }) {
  const color = tone === 'ready' ? 'var(--ready)' : tone === 'pending' ? 'var(--pending)' : 'var(--ink)'
  return (
    <span className="font-type" style={{ border: '1px solid var(--line)', borderRadius: 2, padding: '.25rem .6rem', fontSize: '.78rem', background: 'var(--paper2)' }}>
      <b style={{ color }}>{value}</b> <span style={{ color: 'var(--pencil)' }}>{label}</span>
    </span>
  )
}
