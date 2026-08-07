import { useRef, useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { DeliveryDetailResp } from '../lib/api'

// The supplier-format mapper. Suppliers send different sheet shapes — Код/Наименование/
// Количество/Цена, or with Баркод, or Код/Наименование/Количество/Баркод and no price — so
// instead of guessing, the user maps each column ONCE and can save it per supplier for next
// time. This also kills the blank-first-row bug: the user sees the real rows and picks the
// start row, and the saved start row is remembered too.
//
// Flow: pick file → /preview (reads first rows, no create) → map columns + start row →
// /api/deliveries with the mapping. A saved profile pre-fills the mapping; auto-detect fills
// it otherwise.

type Suggested = { code: number; name: number; qty: number; price: number; barcode: number; data_row: number }
type Profile = { data_row: number; col_code: number; col_name: number; col_qty: number; col_price: number; col_barcode: number }
type Preview = { columns: string[]; rows: string[][]; suggested: Suggested; profile: Profile | null }

type FieldKey = 'code' | 'name' | 'qty' | 'price' | 'barcode'
const FIELDS: { key: FieldKey; label: string; required?: boolean }[] = [
  { key: 'code', label: 'Код' },
  { key: 'name', label: 'Наименование', required: true },
  { key: 'qty', label: 'Количество', required: true },
  { key: 'price', label: 'Цена' },
  { key: 'barcode', label: 'Баркод' },
]

const ABSENT = -1

export function DeliveryImportMapper({ supplier, depot, reference, docDate, onCreated }: {
  supplier: string; depot: number; reference: string; docDate: string
  onCreated: (id: number) => void
}) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [pre, setPre] = useState<Preview | null>(null)
  const [map, setMap] = useState<Record<FieldKey, number>>({ code: ABSENT, name: ABSENT, qty: ABSENT, price: ABSENT, barcode: ABSENT })
  const [dataRow, setDataRow] = useState(1)
  const [saveProfile, setSaveProfile] = useState(true)
  const [savedNote, setSavedNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const pickFile = async (f?: File) => {
    if (!f) return
    setFile(f); setBusy(true); setError(''); setPre(null); setSavedNote('')
    try {
      const form = new FormData()
      form.append('file', f); form.append('supplier', supplier)
      const p = await api.upload<Preview>('/api/deliveries/preview', form)
      setPre(p)
      const src = p.profile
        ? { code: p.profile.col_code, name: p.profile.col_name, qty: p.profile.col_qty, price: p.profile.col_price, barcode: p.profile.col_barcode, data_row: p.profile.data_row }
        : p.suggested
      setMap({ code: src.code, name: src.name, qty: src.qty, price: src.price, barcode: src.barcode })
      setDataRow(src.data_row || 1)
      if (p.profile) setSavedNote(`Зареден запаметен формат за „${supplier || 'този доставчик'}“.`)
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Не мога да прочета файла.') } finally { setBusy(false) }
  }

  const create = async () => {
    if (!file) return
    if (map.name < 0 || map.qty < 0) { setError('Посочи поне колоните „Наименование“ и „Количество“.'); return }
    setBusy(true); setError('')
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('supplier', supplier); form.append('depot_id', String(depot))
      form.append('reference', reference); form.append('doc_date', docDate)
      form.append('col_code', String(map.code)); form.append('col_name', String(map.name))
      form.append('col_qty', String(map.qty)); form.append('col_price', String(map.price))
      form.append('col_barcode', String(map.barcode)); form.append('data_row', String(dataRow))
      form.append('save_profile', saveProfile ? 'true' : 'false')
      const b = await api.upload<DeliveryDetailResp>('/api/deliveries', form)
      onCreated(b.id)
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Създаването се провали.') } finally { setBusy(false) }
  }

  return (
    <div style={{ display: 'grid', gap: '.6rem' }}>
      <div style={{ display: 'flex', gap: '.6rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <input ref={fileRef} type="file" accept=".xlsx,.xlsm,.csv" style={{ display: 'none' }} onChange={(e) => void pickFile(e.target.files?.[0])} />
        <button className="btn-paper btn-red" onClick={() => fileRef.current?.click()} disabled={busy}>
          {busy && !pre ? 'чете…' : file ? 'Друг файл' : 'Качи таблица'}
        </button>
        <span className="font-type" style={{ fontSize: '.74rem', color: 'var(--pencil)' }}>
          Excel (.xlsx) или CSV — ще посочиш коя колона какво е
        </span>
      </div>

      {savedNote && <div className="font-type" style={{ fontSize: '.76rem', color: 'var(--ready)' }}>{savedNote}</div>}

      {pre && (
        <>
          {/* which column is which */}
          <div style={{ display: 'flex', gap: '.7rem', flexWrap: 'wrap' }}>
            {FIELDS.map(({ key, label, required }) => (
              <label key={key} style={{ display: 'grid', gap: '.15rem' }}>
                <span className="font-type" style={{ fontSize: '.62rem', color: 'var(--red)', textTransform: 'uppercase', letterSpacing: '.05em' }}>
                  {label}{required ? ' *' : ''}
                </span>
                <select className="input-paper" value={map[key]} onChange={(e) => setMap((m) => ({ ...m, [key]: Number(e.target.value) }))}>
                  <option value={ABSENT}>— няма —</option>
                  {pre.columns.map((c, i) => <option key={i} value={i}>{c}</option>)}
                </select>
              </label>
            ))}
            <label style={{ display: 'grid', gap: '.15rem' }}>
              <span className="font-type" style={{ fontSize: '.62rem', color: 'var(--red)', textTransform: 'uppercase', letterSpacing: '.05em' }}>Данните почват от ред №</span>
              <input className="input-paper" type="number" min={1} value={dataRow} onChange={(e) => setDataRow(Math.max(1, Number(e.target.value) || 1))} style={{ width: '6rem' }} />
            </label>
          </div>

          {/* the first rows, so the mapping is a WYSIWYG choice, not a guess */}
          <div style={{ overflowX: 'auto', border: '1px solid var(--line)', borderRadius: 3 }}>
            <table className="font-type" style={{ borderCollapse: 'collapse', fontSize: '.72rem', width: '100%' }}>
              <thead>
                <tr style={{ background: 'var(--paper2)' }}>
                  <th style={{ padding: '.2rem .35rem', color: 'var(--pencil)' }}>#</th>
                  {pre.columns.map((c, i) => {
                    const used = FIELDS.find((f) => map[f.key] === i)
                    return (
                      <th key={i} style={{ padding: '.2rem .45rem', textAlign: 'left', color: used ? 'var(--red)' : 'var(--pencil)', whiteSpace: 'nowrap' }}>
                        {c}{used ? ` · ${used.label}` : ''}
                      </th>
                    )
                  })}
                </tr>
              </thead>
              <tbody>
                {pre.rows.map((r, ri) => {
                  const rowNo = ri + 1                       // 1-based, matches the spreadsheet
                  const isData = rowNo >= dataRow
                  return (
                    <tr key={ri} style={{ borderTop: '1px dashed var(--line)', opacity: isData ? 1 : 0.45 }}>
                      <td style={{ padding: '.2rem .35rem', color: isData ? 'var(--ready)' : 'var(--pencil)', fontWeight: isData ? 700 : 400 }}>{rowNo}</td>
                      {pre.columns.map((_, ci) => (
                        <td key={ci} style={{ padding: '.2rem .45rem', whiteSpace: 'nowrap', maxWidth: '12rem', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                          {r[ci] ?? ''}
                        </td>
                      ))}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>

          <label style={{ display: 'flex', gap: '.4rem', alignItems: 'center' }}>
            <input type="checkbox" checked={saveProfile} onChange={(e) => setSaveProfile(e.target.checked)} />
            <span className="font-type" style={{ fontSize: '.8rem' }}>
              Запомни този формат за „{supplier || 'този доставчик'}“ {supplier ? '' : '(въведи доставчик, за да се запамети)'}
            </span>
          </label>

          <button className="btn-paper btn-red" onClick={() => void create()} disabled={busy} style={{ justifySelf: 'start' }}>
            {busy ? 'обработва…' : 'Обработи доставката'}
          </button>
        </>
      )}
      {error && <div style={{ color: 'var(--red)', fontFamily: 'var(--type)', fontSize: '.82rem' }}>{error}</div>}
    </div>
  )
}
