import { useEffect, useMemo, useRef, useState, useCallback } from 'react'
import { api, ApiError } from '../lib/api'
import type { CacheProduct, CacheSearchResp, CacheFacets, BulkPricePreview } from '../lib/api'
import { Sheet } from '../components/Paper'
import { Loading } from '../components/PageHeader'

// Filterable + selectable product table with bulk price operations. Runs entirely off the local
// product cache (instant), so filtering by name/supplier/category/price/attribute doesn't hit Barsy.
// Select products → preview a price operation (×/÷/+/−/set) → apply (owner-only, write-through).
const eur = (n: number | null) => n == null ? '—' : n.toLocaleString('bg-BG', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' €'
const PER = 200

export function ProductBulkPricing() {
  const [facets, setFacets] = useState<CacheFacets | null>(null)
  const [q, setQ] = useState('')
  const [supplier, setSupplier] = useState('')
  const [category, setCategory] = useState('')
  const [priceMin, setPriceMin] = useState('')
  const [priceMax, setPriceMax] = useState('')
  const [lowOnly, setLowOnly] = useState(false)
  const [attrs, setAttrs] = useState<Record<string, string>>({})   // key -> value
  const [data, setData] = useState<CacheSearchResp | null>(null)
  const [loading, setLoading] = useState(false)
  const [sel, setSel] = useState<Set<number>>(new Set())
  const seqRef = useRef(0)
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  useEffect(() => { api.get<CacheFacets>('/api/catalog/facets').then(setFacets).catch(() => setFacets({ suppliers: [], categories: [], attributes: {} })) }, [])

  const query = useMemo(() => {
    const p = new URLSearchParams()
    if (q.trim()) p.set('q', q.trim())
    if (supplier) p.set('supplier', supplier)
    if (category) p.set('category', category)
    if (priceMin) p.set('price_min', priceMin)
    if (priceMax) p.set('price_max', priceMax)
    if (lowOnly) p.set('low_only', 'true')
    Object.entries(attrs).forEach(([k, v]) => { if (v) p.append('attr', `${k}=${v}`) })
    p.set('per_page', String(PER))
    return p.toString()
  }, [q, supplier, category, priceMin, priceMax, lowOnly, attrs])

  const load = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current)
    const seq = ++seqRef.current
    setLoading(true)
    timerRef.current = setTimeout(() => {
      api.get<CacheSearchResp>(`/api/catalog/search?${query}`)
        .then((d) => { if (seq === seqRef.current) { setData(d); setLoading(false) } })
        .catch(() => { if (seq === seqRef.current) { setData({ total: 0, page: 1, per_page: PER, items: [] }); setLoading(false) } })
    }, 250)
  }, [query])
  useEffect(() => { load() }, [load])
  useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current) }, [])

  const items = data?.items || []
  const allSelected = items.length > 0 && items.every((i) => sel.has(i.article_id))
  const toggle = (id: number) => setSel((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n })
  const toggleAll = () => setSel((s) => {
    if (items.every((i) => s.has(i.article_id))) { const n = new Set(s); items.forEach((i) => n.delete(i.article_id)); return n }
    const n = new Set(s); items.forEach((i) => n.add(i.article_id)); return n
  })
  const selItems = items.filter((i) => sel.has(i.article_id))

  return (
    <div style={{ display: 'grid', gap: '.8rem' }}>
      <Sheet>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '.6rem', flexWrap: 'wrap', marginBottom: '.5rem' }}>
          <h3 style={{ fontSize: '1rem' }}>Масови операции с цени</h3>
          <CacheSync onSynced={load} />
        </div>
        {/* filter bar */}
        <div style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
          <input className="input-paper" style={{ width: '13rem' }} placeholder="търси име / код…" value={q} onChange={(e) => setQ(e.target.value)} />
          <select className="input-paper" value={supplier} onChange={(e) => setSupplier(e.target.value)}>
            <option value="">— доставчик —</option>
            {facets?.suppliers.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <select className="input-paper" value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="">— категория —</option>
            {facets?.categories.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <input className="input-paper" style={{ width: '6rem' }} inputMode="decimal" placeholder="цена ≥" value={priceMin} onChange={(e) => setPriceMin(e.target.value)} />
          <input className="input-paper" style={{ width: '6rem' }} inputMode="decimal" placeholder="цена ≤" value={priceMax} onChange={(e) => setPriceMax(e.target.value)} />
          <label className="font-type" style={{ fontSize: '.74rem', color: 'var(--pencil)', display: 'flex', gap: '.3rem', alignItems: 'center', cursor: 'pointer' }}>
            <input type="checkbox" checked={lowOnly} onChange={(e) => setLowOnly(e.target.checked)} /> ниска налич.
          </label>
        </div>
        {/* attribute filters */}
        {facets && Object.keys(facets.attributes).length > 0 && (
          <div style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap', marginTop: '.5rem' }}>
            {Object.entries(facets.attributes).map(([k, vals]) => (
              <select key={k} className="input-paper" style={{ fontSize: '.74rem' }} value={attrs[k] || ''}
                onChange={(e) => setAttrs((a) => ({ ...a, [k]: e.target.value }))}>
                <option value="">— {k} —</option>
                {vals.map((v) => <option key={v} value={v}>{v}</option>)}
              </select>
            ))}
          </div>
        )}
        <div className="font-type" style={{ fontSize: '.74rem', color: 'var(--pencil)', marginTop: '.5rem' }}>
          {data ? `${data.total} продукта${data.total > PER ? ` (показани ${PER})` : ''}` : ''} · избрани {sel.size}
        </div>
      </Sheet>

      {sel.size > 0 && <BulkBar selected={selItems} onDone={() => { setSel(new Set()); load() }} />}

      <Sheet>
        {loading && !data ? <Loading /> : (
          <div style={{ overflowX: 'auto' }}>
            <table className="font-type" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.78rem' }}>
              <thead><tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--red)', textAlign: 'left' }}>
                <th style={{ padding: '.25rem' }}><input type="checkbox" checked={allSelected} onChange={toggleAll} aria-label="избери всички" /></th>
                <th style={{ padding: '.25rem' }}>Продукт</th><th style={{ padding: '.25rem' }}>Код</th>
                <th style={{ padding: '.25rem' }}>Доставчик</th><th style={{ padding: '.25rem', textAlign: 'right' }}>Налич.</th>
                <th style={{ padding: '.25rem', textAlign: 'right' }}>Цена</th>
              </tr></thead>
              <tbody>
                {items.map((p) => (
                  <tr key={p.article_id} style={{ borderBottom: '1px dashed var(--line)', background: sel.has(p.article_id) ? 'var(--paper2)' : undefined }}>
                    <td style={{ padding: '.25rem' }}><input type="checkbox" checked={sel.has(p.article_id)} onChange={() => toggle(p.article_id)} aria-label={p.name} /></td>
                    <td style={{ padding: '.3rem .25rem' }}>{p.name || '(без име)'}
                      {p.attributes.length > 0 && <span style={{ color: 'var(--pencil)' }}> · {p.attributes.map((a) => `${a.value}`).join(', ')}</span>}</td>
                    <td style={{ padding: '.3rem .25rem', color: 'var(--pencil)' }}>{p.code}</td>
                    <td style={{ padding: '.3rem .25rem', color: 'var(--pencil)', maxWidth: '10rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={p.supplier}>{p.supplier || '—'}</td>
                    <td style={{ padding: '.3rem .25rem', textAlign: 'right', color: p.low ? 'var(--blocked)' : undefined }}>{p.stock ?? '—'}</td>
                    <td style={{ padding: '.3rem .25rem', textAlign: 'right', fontWeight: 700 }}>{eur(p.price)}</td>
                  </tr>
                ))}
                {items.length === 0 && <tr><td colSpan={6} style={{ padding: '.6rem', color: 'var(--pencil)' }}>Няма продукти за този филтър (или кешът не е синхронизиран).</td></tr>}
              </tbody>
            </table>
          </div>
        )}
      </Sheet>
    </div>
  )
}

// syncs the local cache from Barsy + shows how fresh it is
function CacheSync({ onSynced }: { onSynced: () => void }) {
  const [status, setStatus] = useState<{ count: number; last_sync: string | null } | null>(null)
  const [busy, setBusy] = useState(false)
  const refresh = () => api.get<{ count: number; last_sync: string | null }>('/api/catalog/cache-status').then(setStatus).catch(() => {})
  useEffect(() => { refresh() }, [])
  const sync = async () => {
    setBusy(true)
    try { await api.post('/api/catalog/sync'); await refresh(); onSynced() }
    catch { /* ignore */ } finally { setBusy(false) }
  }
  return (
    <div style={{ display: 'flex', gap: '.5rem', alignItems: 'center' }}>
      <span className="font-type" style={{ fontSize: '.72rem', color: 'var(--pencil)' }}>
        {status ? `${status.count} в кеша${status.last_sync ? ` · ${status.last_sync.slice(0, 16).replace('T', ' ')}` : ''}` : ''}
      </span>
      <button className="btn-paper" style={{ fontSize: '.72rem' }} onClick={() => void sync()} disabled={busy}>{busy ? 'синхр…' : '↻ Синхронизирай'}</button>
    </div>
  )
}

function BulkBar({ selected, onDone }: { selected: CacheProduct[]; onDone: () => void }) {
  const [op, setOp] = useState<'mul' | 'div' | 'add' | 'sub' | 'set'>('mul')
  const [value, setValue] = useState('')
  const [preview, setPreview] = useState<BulkPricePreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const ids = selected.map((s) => s.article_id)
  const OPS: [typeof op, string][] = [['mul', '× умножи'], ['div', '÷ раздели'], ['add', '+ добави'], ['sub', '− извади'], ['set', '= задай']]

  const runPreview = async () => {
    const v = Number(value)
    if (!value || Number.isNaN(v)) { setMsg('Въведи число.'); return }
    setBusy(true); setMsg('')
    try { setPreview(await api.post<BulkPricePreview>('/api/catalog/bulk-price', { article_ids: ids, op, value: v })) }
    catch (e) { setMsg(e instanceof ApiError ? e.message : 'Грешка.') } finally { setBusy(false) }
  }
  const apply = async () => {
    if (!window.confirm(`Прилагане на новата цена върху ${ids.length} продукта в Barsy?`)) return
    setBusy(true); setMsg('')
    try {
      const r = await api.post<BulkPricePreview>('/api/catalog/bulk-price', { article_ids: ids, op, value: Number(value), confirm: true })
      setMsg(`Приложено на ${r.applied} продукта${r.failed && r.failed.length ? ` (неуспешни: ${r.failed.length})` : ''}.`)
      setPreview(null); onDone()
    } catch (e) { setMsg(e instanceof ApiError ? e.message : 'Записът се провали.') } finally { setBusy(false) }
  }

  return (
    <Sheet clip="right">
      <div style={{ display: 'flex', gap: '.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <b className="font-type" style={{ fontSize: '.82rem' }}>{ids.length} избрани · нова цена:</b>
        <select className="input-paper" value={op} onChange={(e) => { setOp(e.target.value as typeof op); setPreview(null) }}>
          {OPS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <input className="input-paper" style={{ width: '7rem' }} inputMode="decimal" placeholder={op === 'set' ? 'нова цена €' : op === 'mul' || op === 'div' ? 'коеф.' : '€'}
          value={value} onChange={(e) => { setValue(e.target.value); setPreview(null) }} />
        <button className="btn-paper" onClick={() => void runPreview()} disabled={busy}>Прегледай</button>
        {preview?.dry_run && <button className="btn-paper btn-red" onClick={() => void apply()} disabled={busy}>Приложи на {preview.count}</button>}
        <span className="font-type" style={{ fontSize: '.72rem', color: 'var(--pencil)' }} title="Изисква надграждане на Barsy плана (ценови правила все още не са отключени).">промоция за период — след надграждане на Barsy</span>
      </div>
      {msg && <div className="font-type" style={{ fontSize: '.8rem', color: msg.includes('Приложено') ? 'var(--ready)' : 'var(--red)', marginTop: '.4rem' }}>{msg}</div>}
      {preview?.dry_run && preview.changes && (
        <div style={{ marginTop: '.6rem', maxHeight: '16rem', overflowY: 'auto', display: 'grid', gap: '.15rem' }}>
          {preview.changes.slice(0, 100).map((c) => (
            <div key={c.article_id} className="font-type" style={{ display: 'flex', justifyContent: 'space-between', fontSize: '.76rem', borderBottom: '1px dashed var(--line)', padding: '.15rem 0' }}>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '22rem' }}>{c.name} <span style={{ color: 'var(--pencil)' }}>{c.code}</span></span>
              <span>{eur(c.old_price)} → <b style={{ color: 'var(--red)' }}>{eur(c.new_price)}</b></span>
            </div>
          ))}
          {preview.changes.length > 100 && <span className="pencil" style={{ fontSize: '.72rem' }}>…и още {preview.changes.length - 100}</span>}
        </div>
      )}
    </Sheet>
  )
}
