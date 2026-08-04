import { useEffect, useState } from 'react'
import { api } from '../lib/api'
import { TextInput, Select } from './form/inputs'

// Suppliers of THIS product: who stocks it, under what code of theirs, what they call it.
// Rows are explicit statements (add/remove by staff); suggestions come from the supplier
// catalogue by our product code and are added only by click.
export function SupplierTable({ draftId }: { draftId: number }) {
  type SupRow = { id: number; supplier: string; supplier_code: string; supplier_name: string; is_default: boolean; delivery_price: string }
  type SupIn = { supplier: string; supplier_code: string; supplier_name: string; is_default?: boolean; delivery_price?: string }
  const [rows, setRows] = useState<SupRow[]>([])
  const [known, setKnown] = useState<string[]>([])
  const [nsup, setNsup] = useState('')
  const [ncode, setNcode] = useState('')
  const [nname, setNname] = useState('')
  const [nprice, setNprice] = useState('')
  const [ndefault, setNdefault] = useState(false)
  const [priceEdits, setPriceEdits] = useState<Record<number, string>>({})
  const load = () => api.get<{ suppliers: SupRow[] }>(`/api/intake/drafts/${draftId}/suppliers`)
    .then((r) => setRows(Array.isArray(r.suppliers) ? r.suppliers : [])).catch(() => { /* endpoint pre-deploy */ })
  useEffect(() => { void load() }, [draftId])   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { api.get<{ suppliers: string[] }>('/api/intake/suppliers/known')
    .then((r) => setKnown(Array.isArray(r.suppliers) ? r.suppliers : [])).catch(() => setKnown([])) }, [])
  const post = (body: SupIn) => api.post(`/api/intake/drafts/${draftId}/suppliers`, body)
  const add = async () => {
    if (!nsup.trim()) return
    try {
      await post({ supplier: nsup, supplier_code: ncode, supplier_name: nname, is_default: ndefault, delivery_price: nprice })
      setNsup(''); setNcode(''); setNname(''); setNprice(''); setNdefault(false); await load()
    } catch { /* keep inputs so nothing is lost */ }
  }
  // an edit restates the WHOLE row (the POST edit-path replaces fields), then reloads
  const patchRow = async (r: SupRow, patch: Partial<SupIn>) => {
    try {
      await post({ supplier: r.supplier, supplier_code: r.supplier_code, supplier_name: r.supplier_name,
        is_default: r.is_default, delivery_price: r.delivery_price, ...patch })
      await load()
    } catch { /* noop */ }
  }
  const del = async (id: number) => { try { await api.del(`/api/intake/drafts/${draftId}/suppliers/${id}`); await load() } catch { /* noop */ } }
  const priceVal = (r: SupRow) => (r.id in priceEdits ? priceEdits[r.id] : r.delivery_price || '')
  const commitPrice = async (r: SupRow) => {
    if (!(r.id in priceEdits)) return
    const v = priceEdits[r.id]
    setPriceEdits((pe) => { const n = { ...pe }; delete n[r.id]; return n })
    if (v !== (r.delivery_price || '')) await patchRow(r, { delivery_price: v })
  }
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
                <input type="radio" name={`sup-default-${draftId}`} checked={r.is_default}
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
                <button className="btn-paper" style={{ fontSize: '.7rem' }} onClick={() => void del(r.id)}>махни</button>
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
              <input type="radio" name={`sup-default-${draftId}`} checked={ndefault}
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
