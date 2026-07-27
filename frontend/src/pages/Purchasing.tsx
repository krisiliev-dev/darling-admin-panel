import { useRef, useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { BarsyArticle, POLine, PurchaseOrder, ReorderItem, Supplier } from '../lib/api'
import { useApi } from '../lib/useApi'
import { useHotkey, isTyping } from '../lib/useHotkey'
import { Sheet } from '../components/Paper'
import { PageHeader, EmptyNote, Loading } from '../components/PageHeader'
import { useConfirm } from '../components/Confirm'

// PO line while editing (amount/current_price are strings so decimals type cleanly).
interface EditLine { article_id: number; name: string; amount: string; current_price: string }

// Доставки — supplier orders, Barsy-first. Заявки are draft storeloads in Barsy; receiving a
// delivery closes the storeload (stock updates in Barsy).
export function Purchasing({ embedded = false }: { embedded?: boolean } = {}) {
  const { data: sup } = useApi<{ suppliers: Supplier[] }>('/api/purchasing/suppliers')
  const { data: reorder } = useApi<{ items: ReorderItem[] }>('/api/purchasing/reorder')
  const { data: orders, reload: reloadOrders } = useApi<{ orders: PurchaseOrder[] }>('/api/purchasing/orders')
  const [supplierId, setSupplierId] = useState(0)

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      {!embedded && <PageHeader title="Доставки" subtitle="заявки към доставчици (в Barsy)" />}

      <Sheet clip="left" tilt="l">
        <h3 style={{ fontSize: '1rem', marginBottom: '.6rem' }}>Доставчици</h3>
        {!sup ? <Loading /> : sup.suppliers.length === 0 ? <EmptyNote>Няма доставчици в Barsy.</EmptyNote> : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(9rem, 1fr))', gap: '.5rem' }}>
            {sup.suppliers.map((s) => (
              <button key={s.supplier_id} onClick={() => setSupplierId(s.supplier_id)}
                className="font-type"
                style={{
                  textAlign: 'left', border: '1px solid var(--line)', borderRadius: 2, padding: '.5rem .6rem', fontSize: '.8rem',
                  background: supplierId === s.supplier_id ? 'var(--paper)' : 'var(--paper2)',
                  borderLeft: `3px solid ${supplierId === s.supplier_id ? 'var(--red)' : 'transparent'}`, cursor: 'pointer',
                }}>{s.supplier_name}</button>
            ))}
          </div>
        )}
      </Sheet>

      <Sheet>
        <h3 style={{ fontSize: '1rem', marginBottom: '.5rem' }}>Предложения за поръчка</h3>
        {!reorder ? <Loading /> : reorder.items.length === 0 ? (
          <EmptyNote>
            Няма продукти под минимума. Щом Barsy има наличности, тук ще излизат артикулите за
            дозареждане с предложено количество.
          </EmptyNote>
        ) : (
          <div style={{ display: 'grid', gap: '.3rem' }}>
            {reorder.items.map((it) => (
              <div key={it.article_id} style={{ display: 'grid', gridTemplateColumns: '1fr 6rem 6rem 7rem', gap: '.5rem', fontSize: '.82rem', padding: '.3rem 0', borderBottom: '1px dashed var(--line)' }}>
                <span>{it.name}</span>
                <span className="font-type" style={{ textAlign: 'right', color: 'var(--blocked)' }}>{it.amount} {it.unit}</span>
                <span className="font-type" style={{ textAlign: 'right', color: 'var(--pencil)' }}>мин {it.min_amount}</span>
                <span className="font-type" style={{ textAlign: 'right', color: 'var(--ready)' }}>поръчай {it.suggested_qty}</span>
              </div>
            ))}
          </div>
        )}
      </Sheet>

      <POBuilder supplierId={supplierId} setSupplierId={setSupplierId}
        suppliers={sup?.suppliers || []} onSaved={reloadOrders} />

      <OrdersList orders={orders?.orders || null} onChange={reloadOrders} />
    </div>
  )
}

function POBuilder({
  supplierId, setSupplierId, suppliers, onSaved,
}: {
  supplierId: number; setSupplierId: (id: number) => void
  suppliers: Supplier[]; onSaved: () => void
}) {
  const [q, setQ] = useState('')
  const [results, setResults] = useState<BarsyArticle[]>([])
  const [lines, setLines] = useState<EditLine[]>([])
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const searchRef = useRef<HTMLInputElement>(null)
  useHotkey((e) => e.key === '/' && !isTyping(e), () => searchRef.current?.focus())

  const search = async () => {
    const d = await api.get<{ articles: BarsyArticle[] }>(`/api/purchasing/articles?q=${encodeURIComponent(q)}`)
    setResults(d.articles)
  }
  const addLine = (a: BarsyArticle) => {
    if (lines.some((l) => l.article_id === a.article_id)) return
    // amount/current_price held as strings so decimals can be typed; parsed at save
    setLines([...lines, { article_id: a.article_id, name: a.name, amount: '1', current_price: String(a.price ?? 0) }])
  }
  const patchLine = (i: number, p: Partial<EditLine>) => setLines(lines.map((l, j) => (j === i ? { ...l, ...p } : l)))
  const total = lines.reduce((s, l) => s + (parseFloat(l.amount) || 0) * (parseFloat(l.current_price) || 0), 0)

  const save = async (close: boolean) => {
    if (!supplierId || lines.length === 0) { setMsg('Избери доставчик и добави продукти.'); return }
    setBusy(true); setMsg('')
    try {
      const payload: POLine[] = lines.map((l) => ({
        article_id: l.article_id, name: l.name,
        amount: parseFloat(l.amount) || 0, current_price: parseFloat(l.current_price) || 0,
      }))
      const r = await api.post<{ storeload_id: number; status: string }>('/api/purchasing/orders',
        { supplier_id: supplierId, note, lines: payload, close })
      setLines([]); setNote(''); setResults([]); setQ('')
      setMsg(close ? `Заредено в Barsy (№ ${r.storeload_id}).` : `Заявка създадена в Barsy (чернова № ${r.storeload_id}).`)
      onSaved()
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : 'Записът се провали.')
    } finally { setBusy(false) }
  }

  return (
    <Sheet clip="right" tilt="r">
      <h3 style={{ fontSize: '1rem', marginBottom: '.5rem' }}>Нова заявка</h3>
      <div style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap', alignItems: 'center', marginBottom: '.6rem' }}>
        <select className="input-paper" style={{ width: 'auto' }} aria-label="доставчик"
          value={supplierId} onChange={(e) => setSupplierId(Number(e.target.value))}>
          <option value={0}>— избери доставчик —</option>
          {suppliers.map((s) => <option key={s.supplier_id} value={s.supplier_id}>{s.supplier_name}</option>)}
        </select>
        <input ref={searchRef} className="input-paper" style={{ width: '14rem' }} placeholder="търси артикул (Barsy)  ( / )" value={q}
          onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') void search() }} />
        <button className="btn-paper" onClick={() => void search()}>Търси</button>
      </div>

      {results.length > 0 && (
        <div style={{ maxHeight: '11rem', overflowY: 'auto', display: 'grid', gap: '.2rem', marginBottom: '.7rem', border: '1px solid var(--line)', borderRadius: 2, padding: '.4rem' }}>
          {results.map((a) => (
            <button key={a.article_id} onClick={() => addLine(a)}
              style={{ display: 'flex', justifyContent: 'space-between', gap: '.5rem', textAlign: 'left', border: 'none', background: 'transparent', cursor: 'pointer', fontSize: '.8rem', padding: '.2rem .3rem' }}>
              <span>{a.name} <span style={{ color: 'var(--pencil)' }}>{a.code}</span></span>
              <span className="font-type" style={{ color: 'var(--red)' }}>+ добави</span>
            </button>
          ))}
        </div>
      )}

      {lines.length > 0 && (
        <div style={{ display: 'grid', gap: '.3rem', marginBottom: '.6rem' }}>
          <div className="font-type" style={{ display: 'grid', gridTemplateColumns: '1fr 5rem 6rem 5rem 2rem', gap: '.4rem', fontSize: '.6rem', textTransform: 'uppercase', color: 'var(--red)', letterSpacing: '.06em' }}>
            <span>Артикул</span><span style={{ textAlign: 'right' }}>Кол.</span><span style={{ textAlign: 'right' }}>Дост. цена €</span><span style={{ textAlign: 'right' }}>Сума</span><span />
          </div>
          {lines.map((l, i) => (
            <div key={l.article_id} style={{ display: 'grid', gridTemplateColumns: '1fr 5rem 6rem 5rem 2rem', gap: '.4rem', alignItems: 'center', fontSize: '.82rem' }}>
              <span>{l.name}</span>
              <input className="input-paper" style={{ textAlign: 'right' }} inputMode="decimal" aria-label="количество" value={l.amount}
                onChange={(e) => patchLine(i, { amount: e.target.value })} />
              <input className="input-paper" style={{ textAlign: 'right' }} inputMode="decimal" aria-label="доставна цена" value={l.current_price}
                onChange={(e) => patchLine(i, { current_price: e.target.value })} />
              <span className="font-type" style={{ textAlign: 'right' }}>{((parseFloat(l.amount) || 0) * (parseFloat(l.current_price) || 0)).toFixed(2)}</span>
              <button onClick={() => setLines(lines.filter((_, j) => j !== i))} style={{ border: 'none', background: 'transparent', cursor: 'pointer', color: 'var(--blocked)' }} aria-label="премахни">✕</button>
            </div>
          ))}
          <div className="font-type" style={{ textAlign: 'right', fontWeight: 700, marginTop: '.2rem' }}>Общо: {total.toFixed(2)} €</div>
        </div>
      )}

      <input className="input-paper" placeholder="бележка / № документ (по избор)" value={note} onChange={(e) => setNote(e.target.value)} style={{ marginBottom: '.5rem' }} />
      <div style={{ display: 'flex', gap: '.6rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn-paper btn-red" onClick={() => void save(false)} disabled={busy || !lines.length}>
          {busy ? 'записва…' : 'Заявка (чернова)'}
        </button>
        <button className="btn-paper" onClick={() => void save(true)} disabled={busy || !lines.length}
          title="създава и зарежда наличностите в Barsy">Заведи като получена</button>
        {msg && <span className="font-type" style={{ fontSize: '.8rem', color: 'var(--pencil)' }}>{msg}</span>}
      </div>
    </Sheet>
  )
}

function OrdersList({ orders, onChange }: { orders: PurchaseOrder[] | null; onChange: () => void }) {
  const confirm = useConfirm()
  const close = async (id: number) => {
    if (!(await confirm({ confirmLabel: 'Приключи', message: 'Приключване на зареждането? Количествата влизат в наличностите в Barsy.' }))) return
    try { await api.post(`/api/purchasing/orders/${id}/close`); onChange() } catch { /* ignore */ }
  }
  if (!orders || orders.length === 0) return null
  return (
    <Sheet>
      <h3 style={{ fontSize: '1rem', marginBottom: '.5rem' }}>Зареждания (Barsy)</h3>
      <div style={{ display: 'grid', gap: '.4rem' }}>
        {orders.map((o) => (
          <div key={o.id} style={{ display: 'grid', gridTemplateColumns: '1fr auto auto', gap: '.7rem', alignItems: 'center', border: '1px solid var(--line)', borderRadius: 2, padding: '.5rem .7rem', background: 'var(--paper2)' }}>
            <div>
              <span className="font-type" style={{ fontWeight: 700 }}>№{o.id} · {o.supplier || o.doc_type}</span>
              <span style={{ color: 'var(--pencil)', fontSize: '.78rem' }}>{o.doc_date ? ` · ${o.doc_date}` : ''}{o.total != null ? ` · ${Number(o.total).toFixed(2)} €` : ''}</span>
            </div>
            <span className="font-type" style={{ fontSize: '.7rem', color: o.status === 'closed' ? 'var(--ready)' : 'var(--pending)' }}>
              {o.status === 'closed' ? 'приключено' : 'чернова'}
            </span>
            {o.status === 'draft' && <button className="btn-paper" style={{ fontSize: '.72rem' }} onClick={() => void close(o.id)}>Приключи</button>}
          </div>
        ))}
      </div>
    </Sheet>
  )
}
