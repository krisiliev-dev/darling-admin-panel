import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../lib/api'

// The goods document and the invoice are different events. A стокова разписка says the things
// arrived; the фактура says they were billed, and routinely turns up days later. This panel is
// where that gap is visible: goods-only documents are marked, so a delivery sitting on a разписка
// with no фактура reads as unfinished paperwork rather than as done.
//
// Every response here is shape-checked before it is dereferenced. The e2e mockApi matches routes by
// SUBSTRING with first match wins, so a new /api/deliveries/* subpath silently receives the LIST
// body — a raw .map() on that crashes the whole page under test. This bit us once already.

type Doc = {
  id: number; batch_id: number
  doc_type_id: number; doc_type: string
  invoice_ref: string; doc_date: string; amount: number
  note: string; created_by: string; created_at: string | null
  goods_only: boolean
}
type DocType = { id: number; name: string; goods_only: boolean }

const arr = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : [])

export function DocumentsPanel({ batchId, canDelete }: { batchId: number; canDelete: boolean }) {
  const [docs, setDocs] = useState<Doc[] | null>(null)
  const [types, setTypes] = useState<DocType[]>([])
  const [adding, setAdding] = useState(false)
  const [typeId, setTypeId] = useState(1)
  const [ref, setRef] = useState('')
  const [date, setDate] = useState('')
  const [amount, setAmount] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const d = await api.get<{ documents: Doc[] }>(`/api/deliveries/${batchId}/documents`)
      setDocs(arr<Doc>(d?.documents))
    } catch { setDocs([]) }
  }, [batchId])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    if (!adding || types.length) return
    api.get<{ doc_types: DocType[] }>('/api/deliveries/doc-types')
      .then((d) => setTypes(arr<DocType>(d?.doc_types)))
      .catch(() => setTypes([]))
  }, [adding, types.length])

  const add = async () => {
    if (!ref.trim()) { setError('Липсва номер на документа.'); return }
    setBusy(true); setError('')
    try {
      await api.post(`/api/deliveries/${batchId}/documents`, {
        doc_type_id: typeId, invoice_ref: ref.trim(), doc_date: date,
        amount: Number(amount) || 0,
      })
      setRef(''); setDate(''); setAmount(''); setAdding(false)
      await load()
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Неуспешно.') } finally { setBusy(false) }
  }

  const remove = async (id: number) => {
    setBusy(true); setError('')
    try {
      await api.del(`/api/deliveries/${batchId}/documents/${id}`)
      await load()
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Неуспешно.') } finally { setBusy(false) }
  }

  const hasInvoice = (docs ?? []).some((d) => !d.goods_only)
  const hasGoodsNote = (docs ?? []).some((d) => d.goods_only)

  return (
    <div className="font-type" style={{ marginTop: '.6rem', border: '1px solid var(--line)', borderRadius: 3, padding: '.6rem .7rem', fontSize: '.82rem' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '.5rem', marginBottom: '.4rem' }}>
        <b style={{ flex: 1 }}>Документи</b>
        {!adding && <button className="btn-paper" style={{ fontSize: '.76rem' }} onClick={() => setAdding(true)}>+ Закачи документ</button>}
      </div>

      {docs === null && <div style={{ color: 'var(--pencil)' }}>зарежда…</div>}
      {docs?.length === 0 && <div style={{ color: 'var(--pencil)' }}>Няма закачени документи.</div>}

      {docs && docs.length > 0 && (
        <div style={{ display: 'grid', gap: '.25rem' }}>
          {docs.map((d) => (
            <div key={d.id} style={{ display: 'flex', alignItems: 'center', gap: '.5rem', flexWrap: 'wrap' }}>
              <span style={{ color: d.goods_only ? 'var(--pending)' : 'var(--ink)', minWidth: '9rem' }}>
                {d.doc_type}
              </span>
              <span><b>{d.invoice_ref}</b></span>
              {d.doc_date && <span style={{ color: 'var(--pencil)' }}>{d.doc_date}</span>}
              {d.amount > 0 && <span style={{ color: 'var(--pencil)' }}>{d.amount.toFixed(2)}</span>}
              {canDelete && (
                <button className="btn-paper" style={{ fontSize: '.72rem', padding: '.1rem .35rem' }}
                  disabled={busy} onClick={() => void remove(d.id)} aria-label={`Откачи ${d.invoice_ref}`}>✕</button>
              )}
            </div>
          ))}
        </div>
      )}

      {/* the exact case the join table exists for: goods in, money still to come */}
      {hasGoodsNote && !hasInvoice && (
        <div style={{ marginTop: '.4rem', color: 'var(--pending)' }}>
          Стоката е приета по разписка — фактурата още не е закачена.
        </div>
      )}

      {adding && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '.35rem', marginTop: '.5rem', alignItems: 'center' }}>
          <select value={typeId} onChange={(e) => setTypeId(Number(e.target.value))}
            style={{ fontSize: '.8rem', padding: '.15rem .3rem' }} aria-label="Тип документ">
            {types.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
          <input type="text" value={ref} placeholder="номер" onChange={(e) => setRef(e.target.value)}
            style={{ width: '7rem', fontSize: '.8rem', padding: '.15rem .3rem' }} aria-label="Номер на документа" />
          <input type="date" value={date} onChange={(e) => setDate(e.target.value)}
            style={{ fontSize: '.8rem', padding: '.15rem .3rem' }} aria-label="Дата на документа" />
          <input type="number" step="0.01" min={0} value={amount} placeholder="сума"
            onChange={(e) => setAmount(e.target.value)}
            style={{ width: '5.5rem', fontSize: '.8rem', padding: '.15rem .3rem' }} aria-label="Сума по документа" />
          <button className="btn-paper btn-red" disabled={busy} onClick={() => void add()}>
            {busy ? '…' : 'Закачи'}
          </button>
          <button className="btn-paper" disabled={busy} onClick={() => { setAdding(false); setError('') }}>Отказ</button>
        </div>
      )}

      {error && <div style={{ color: 'var(--red)', marginTop: '.35rem' }}>{error}</div>}
    </div>
  )
}
