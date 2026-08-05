import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { TextInput, Select } from './form/inputs'

// Suppliers of THIS product: who stocks it, under what code of theirs, what they call it.
// Two modes over one UI (#70 slice 5):
//  - draft mode (create flow): rows live on the intake draft; row-level POST/DELETE as before.
//  - article mode (edit page): rows are the LIVE Barsy links; every change PUTs the FULL list to
//    /api/catalog/{id}/suppliers — Articles_save_suppliers is full-replace, and the list the user
//    sees IS the list that gets written.
type SupRow = { id: number; supplier: string; supplier_code: string; supplier_name: string; is_default: boolean; delivery_price: string; supplier_id?: number; package_id?: string }
type SupIn = { supplier: string; supplier_code: string; supplier_name: string; is_default?: boolean; delivery_price?: string }

export function SupplierTable({ draftId, articleId }: { draftId?: number; articleId?: number }) {
  const isArticle = articleId != null
  const [rows, setRows] = useState<SupRow[]>([])
  const [known, setKnown] = useState<string[]>([])
  const [nsup, setNsup] = useState('')
  const [ncode, setNcode] = useState('')
  const [nname, setNname] = useState('')
  const [nprice, setNprice] = useState('')
  const [ndefault, setNdefault] = useState(false)
  const [priceEdits, setPriceEdits] = useState<Record<number, string>>({})
  const load = () => (isArticle
    ? api.get<{ suppliers: SupRow[] }>(`/api/catalog/${articleId}/suppliers`)
      .then((r) => setRows(Array.isArray(r.suppliers) ? r.suppliers : [])).catch(() => { /* offline */ })
    : api.get<{ suppliers: SupRow[] }>(`/api/intake/drafts/${draftId}/suppliers`)
      .then((r) => setRows(Array.isArray(r.suppliers) ? r.suppliers : [])).catch(() => { /* endpoint pre-deploy */ }))
  useEffect(() => { void load() }, [draftId, articleId])   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { api.get<{ suppliers: string[] }>('/api/intake/suppliers/known')
    .then((r) => setKnown(Array.isArray(r.suppliers) ? r.suppliers : [])).catch(() => setKnown([])) }, [])
  const putAll = async (rs: SupRow[]) => {
    try {
      await api.put(`/api/catalog/${articleId}/suppliers`, { rows: rs.map((r) => ({
        supplier_id: r.supplier_id ?? null, supplier: r.supplier, supplier_code: r.supplier_code,
        supplier_name: r.supplier_name, is_default: r.is_default, delivery_price: r.delivery_price || '',
        package_id: r.package_id || '' })) })
      await load()
    } catch { /* keep what is on screen */ }
  }
  const post = (body: SupIn) => api.post(`/api/intake/drafts/${draftId}/suppliers`, body)
  const add = async () => {
    if (!nsup.trim()) return
    if (isArticle) {
      await putAll([...rows, { id: -1, supplier: nsup, supplier_code: ncode, supplier_name: nname, is_default: ndefault, delivery_price: nprice }])
      setNsup(''); setNcode(''); setNname(''); setNprice(''); setNdefault(false)
      return
    }
    try {
      await post({ supplier: nsup, supplier_code: ncode, supplier_name: nname, is_default: ndefault, delivery_price: nprice })
      setNsup(''); setNcode(''); setNname(''); setNprice(''); setNdefault(false); await load()
    } catch { /* keep inputs so nothing is lost */ }
  }
  // an edit restates the WHOLE row; in article mode setting Основен demotes the others client-side
  // (the first flagged row wins server-side, so the OLD default must not stay flagged before it)
  const patchRow = async (r: SupRow, patch: Partial<SupIn>) => {
    if (isArticle) {
      const next = patch.is_default
        ? rows.map((x) => (x === r ? { ...x, ...patch, is_default: true } : { ...x, is_default: false }))
        : rows.map((x) => (x === r ? { ...x, ...patch } : x))
      await putAll(next as SupRow[])
      return
    }
    try {
      await post({ supplier: r.supplier, supplier_code: r.supplier_code, supplier_name: r.supplier_name,
        is_default: r.is_default, delivery_price: r.delivery_price, ...patch })
      await load()
    } catch { /* noop */ }
  }
  const del = async (r: SupRow) => {
    if (isArticle) { await putAll(rows.filter((x) => x !== r)); return }
    try { await api.del(`/api/intake/drafts/${draftId}/suppliers/${r.id}`); await load() } catch { /* noop */ }
  }
  const priceVal = (r: SupRow) => (r.id in priceEdits ? priceEdits[r.id] : r.delivery_price || '')
  const commitPrice = async (r: SupRow) => {
    if (!(r.id in priceEdits)) return
    const v = priceEdits[r.id]
    setPriceEdits((pe) => { const n = { ...pe }; delete n[r.id]; return n })
    if (v !== (r.delivery_price || '')) await patchRow(r, { delivery_price: v })
  }
  const radioName = `sup-default-${isArticle ? 'a' + articleId : draftId}`
  return (
    <div>
      <span className="font-type" style={{ fontSize: '.6rem', color: 'var(--pencil)', textTransform: 'uppercase', letterSpacing: '.05em' }}>Доставчици</span>
      <table style={{ width: '100%', borderCollapse: 'collapse', marginTop: '.3rem', fontSize: '.82rem' }}>
        <thead>
          <tr style={{ textAlign: 'left', color: 'var(--pencil)', fontSize: '.7rem' }}>
            <th style={{ padding: '.2rem .3rem' }}>Доставчик</th>
            <th style={{ padding: '.2rem .3rem' }}>Негов код</th>
            <th style={{ padding: '.2rem .3rem' }}>Негово име на продукта</th>
            <th style={{ padding: '.2rem .3rem', textAlign: 'center' }}>Основен</th>
            <th style={{ padding: '.2rem .3rem' }}>Договорена цена</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} style={{ borderTop: '1px solid var(--line)' }}>
              <td style={{ padding: '.25rem .3rem', fontWeight: 700 }}>{r.supplier}</td>
              <td style={{ padding: '.25rem .3rem', fontFamily: 'monospace' }}>{r.supplier_code || '—'}</td>
              <td style={{ padding: '.25rem .3rem' }}>{r.supplier_name || '—'}</td>
              <td style={{ padding: '.25rem .3rem', textAlign: 'center' }}>
                <input type="radio" name={radioName} checked={r.is_default}
                  aria-label={`Основен доставчик: ${r.supplier}`}
                  onChange={() => { if (!r.is_default) void patchRow(r, { is_default: true }) }} />
              </td>
              <td style={{ padding: '.25rem .3rem' }}>
                <TextInput inputMode="decimal" width="5.5rem"
                  placeholder="—" value={priceVal(r)}
                  onChange={(e) => setPriceEdits((pe) => ({ ...pe, [r.id]: e.target.value }))}
                  onBlur={() => void commitPrice(r)} />
              </td>
              <td style={{ padding: '.25rem .3rem', textAlign: 'right' }}>
                <button className="btn-paper" style={{ fontSize: '.7rem' }} onClick={() => void del(r)}>махни</button>
              </td>
            </tr>
          ))}
          <tr style={{ borderTop: '1px solid var(--line)' }}>
            <td style={{ padding: '.25rem .3rem' }}>
              <Select value={nsup} onChange={(v) => setNsup(v)} options={known} placeholder="избери доставчик…" />
            </td>
            <td style={{ padding: '.25rem .3rem' }}><TextInput placeholder="код" value={ncode} onChange={(e) => setNcode(e.target.value)} /></td>
            <td style={{ padding: '.25rem .3rem' }}><TextInput placeholder="име при доставчика" value={nname} onChange={(e) => setNname(e.target.value)} /></td>
            <td style={{ padding: '.25rem .3rem', textAlign: 'center' }}>
              <input type="radio" name={radioName} checked={ndefault}
                aria-label="Нов ред: основен доставчик" onChange={() => setNdefault(true)} />
            </td>
            <td style={{ padding: '.25rem .3rem' }}>
              <TextInput inputMode="decimal" width="5.5rem" placeholder="цена"
                value={nprice} onChange={(e) => setNprice(e.target.value)} />
            </td>
            <td style={{ padding: '.25rem .3rem', textAlign: 'right' }}>
              <button className="btn-paper" style={{ fontSize: '.7rem' }} disabled={!nsup.trim()}
                onClick={() => void add()}>+ добави</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  )
}
