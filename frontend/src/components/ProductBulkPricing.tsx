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

      <PromoList />
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

// active Barsy price rules (promotions) — view + archive
function PromoList() {
  const [promos, setPromos] = useState<{ id: number; name: string; period: string; active: boolean }[] | null>(null)
  const load = () => api.get<{ promos: typeof promos }>('/api/catalog/promos').then((d) => setPromos(d.promos || [])).catch(() => setPromos([]))
  useEffect(() => { load() }, [])
  const del = async (id: number) => {
    if (!window.confirm('Премахване на тази промоция?')) return
    try { await api.post(`/api/catalog/promos/${id}/delete`, {}); await load() } catch { /* ignore */ }
  }
  if (!promos) return null
  return (
    <Sheet>
      <h3 style={{ fontSize: '.95rem', marginBottom: '.4rem' }}>Активни промоции ({promos.length})</h3>
      {promos.length === 0 && (
        <p className="font-type" style={{ color: 'var(--pencil)', fontSize: '.8rem' }}>
          Няма активни промоции. Избери продукти по-горе и създай промоция.
        </p>
      )}
      <div style={{ display: 'grid', gap: '.3rem' }}>
        {promos.map((p) => (
          <div key={p.id} className="font-type" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '.8rem', borderBottom: '1px dashed var(--line)', padding: '.2rem 0' }}>
            <span>🏷 <b>{p.name || `#${p.id}`}</b>{p.period ? <span style={{ color: 'var(--pencil)' }}> · {p.period}</span> : ''}</span>
            <button className="btn-paper" style={{ fontSize: '.7rem', color: 'var(--red)' }} onClick={() => void del(p.id)}>премахни</button>
          </div>
        ))}
      </div>
    </Sheet>
  )
}

// native Barsy timed promotion (Pricelists) on the selected products — % / € discount or exact
// promo price, active over a date window; auto-reverts, base price untouched.
function PromoForm({ ids, onDone }: { ids: number[]; onDone: () => void }) {
  const [name, setName] = useState('')
  const [mode, setMode] = useState<'discount_pct' | 'discount_eur' | 'exact'>('discount_pct')
  const [value, setValue] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const create = async () => {
    if (!name.trim() || !value || !from || !to) { setMsg('Попълни име, стойност и период.'); return }
    if (!window.confirm(`Създаване на промоция „${name}“ за ${ids.length} продукта (${from} → ${to})?`)) return
    setBusy(true); setMsg('')
    try {
      const r = await api.post<{ pricelist_id: number; period: string; count: number }>('/api/catalog/promo', {
        article_ids: ids, name, mode, value: Number(value), date_from: from, date_to: to, confirm: true })
      setMsg(`Промоцията е активна за ${r.count} продукта (${r.period}).`); onDone()
    } catch (e) { setMsg(e instanceof ApiError ? e.message : 'Промоцията се провали.') } finally { setBusy(false) }
  }
  const MODES: [typeof mode, string][] = [['discount_pct', '% отстъпка'], ['discount_eur', '€ отстъпка'], ['exact', 'точна промо цена €']]

  return (
    <div style={{ marginTop: '.6rem', border: '1px dashed var(--line)', borderRadius: 3, padding: '.7rem', background: 'var(--paper2)', display: 'grid', gap: '.5rem' }}>
      <div className="font-type" style={{ fontSize: '.8rem', fontWeight: 700 }}>🏷 Промоция за период · {ids.length} продукта</div>
      <div style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
        <input className="input-paper" style={{ width: '12rem' }} placeholder="име (напр. Коледна)" value={name} onChange={(e) => setName(e.target.value)} />
        <select className="input-paper" value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}>
          {MODES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <input className="input-paper" style={{ width: '7rem' }} inputMode="decimal" placeholder={mode === 'discount_pct' ? '% напр. 20' : '€'} value={value} onChange={(e) => setValue(e.target.value)} />
      </div>
      <div style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
        <label className="font-type" style={{ fontSize: '.72rem', color: 'var(--pencil)', display: 'flex', gap: '.3rem', alignItems: 'center' }}>от <input className="input-paper" type="date" value={from} onChange={(e) => setFrom(e.target.value)} /></label>
        <label className="font-type" style={{ fontSize: '.72rem', color: 'var(--pencil)', display: 'flex', gap: '.3rem', alignItems: 'center' }}>до <input className="input-paper" type="date" value={to} onChange={(e) => setTo(e.target.value)} /></label>
        <button className="btn-paper btn-red" onClick={() => void create()} disabled={busy}>{busy ? 'създава…' : 'Създай промоция'}</button>
      </div>
      {msg && <div className="font-type" style={{ fontSize: '.8rem', color: msg.includes('активна') ? 'var(--ready)' : 'var(--red)' }}>{msg}</div>}
    </div>
  )
}

// Bulk RENAME by naming recipe: recompose selected products' names (v3 composer) under a chosen style
// (color/material/shape prefix vs suffix). Preview → apply; optionally save the style as the default.
interface RenamePreview { count: number; changes: { article_id: number; old_name: string; new_name: string }[]; skipped: { name: string; reason: string }[] }
function BulkRenameForm({ ids, onDone }: { ids: number[]; onDone: () => void }) {
  const [options, setOptions] = useState<Record<string, { default: string; variants: string[] }>>({})
  const [style, setStyle] = useState<Record<string, string>>({})
  const [prev, setPrev] = useState<RenamePreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  useEffect(() => { api.get<{ options: typeof options }>('/api/intake/recipe/options').then((d) => setOptions(d.options)).catch(() => { }) }, [])

  const preview = async () => {
    setBusy(true); setMsg('')
    try { setPrev(await api.post<RenamePreview>('/api/catalog/bulk-rename', { article_ids: ids, style })) }
    catch (e) { setMsg(e instanceof ApiError ? e.message : 'Грешка.') } finally { setBusy(false) }
  }
  const apply = async () => {
    if (!window.confirm(`Преименуване на ${prev?.count} продукта в Barsy?`)) return
    setBusy(true); setMsg('')
    try {
      const r = await api.post<{ applied: number; skipped: unknown[] }>('/api/catalog/bulk-rename', { article_ids: ids, style, confirm: true })
      setMsg(`Преименувани ${r.applied} продукта.`); onDone()
    } catch (e) { setMsg(e instanceof ApiError ? e.message : 'Записът се провали.') } finally { setBusy(false) }
  }
  const saveDefault = async () => {
    try { await api.put('/api/intake/recipe', { style }); setMsg('Рецептата е запазена като глобална по подразбиране.') }
    catch { setMsg('Грешка при запис на рецептата.') }
  }

  return (
    <div style={{ marginTop: '.5rem', borderTop: '1px dashed var(--line)', paddingTop: '.5rem', display: 'grid', gap: '.4rem' }}>
      <span className="font-type" style={{ fontSize: '.74rem', color: 'var(--pencil)' }}>Рецепта — как се подреждат атрибутите в името:</span>
      <div style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap' }}>
        {Object.entries(options).map(([attr, cfg]) => (
          <label key={attr} style={{ display: 'grid', gap: '.1rem' }}>
            <span className="font-type" style={{ fontSize: '.6rem', color: 'var(--pencil)' }}>{attr}</span>
            <select className="input-paper" style={{ fontSize: '.74rem' }} value={style[attr] || ''} onChange={(e) => { setStyle((s) => ({ ...s, [attr]: e.target.value })); setPrev(null) }}>
              <option value="">по подразбиране</option>
              {cfg.variants.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          </label>
        ))}
      </div>
      <div style={{ display: 'flex', gap: '.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn-paper" onClick={() => void preview()} disabled={busy}>Прегледай имената</button>
        {prev && prev.count > 0 && <button className="btn-paper btn-red" onClick={() => void apply()} disabled={busy}>Преименувай {prev.count}</button>}
        <button className="btn-paper" onClick={() => void saveDefault()} disabled={busy} title="важи за нови продукти">Запази рецептата глобално</button>
      </div>
      {msg && <div className="font-type" style={{ fontSize: '.78rem', color: msg.includes('Грешка') || msg.includes('провали') ? 'var(--red)' : 'var(--ready)' }}>{msg}</div>}
      {prev && (
        <div style={{ maxHeight: '15rem', overflowY: 'auto', display: 'grid', gap: '.15rem' }}>
          {prev.changes.slice(0, 60).map((c) => (
            <div key={c.article_id} className="font-type" style={{ fontSize: '.75rem', borderBottom: '1px dashed var(--line)', padding: '.15rem 0' }}>
              <span style={{ color: 'var(--pencil)', textDecoration: 'line-through' }}>{c.old_name}</span> → <b>{c.new_name}</b>
            </div>
          ))}
          {prev.count === 0 && <span className="pencil" style={{ fontSize: '.75rem' }}>Няма промени (продуктите нямат Shopify категория или вече са с това име).</span>}
          {prev.skipped.length > 0 && <span className="pencil" style={{ fontSize: '.72rem' }}>Пропуснати: {prev.skipped.length}</span>}
        </div>
      )}
    </div>
  )
}

export function BulkBar({ selected, onDone }: { selected: CacheProduct[]; onDone: () => void }) {
  const [op, setOp] = useState<'mul' | 'div' | 'add' | 'sub' | 'set'>('mul')
  const [value, setValue] = useState('')
  const [preview, setPreview] = useState<BulkPricePreview | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const [showPromo, setShowPromo] = useState(false)
  const [showRename, setShowRename] = useState(false)
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
      const extra = [r.failed && r.failed.length ? `неуспешни: ${r.failed.length}` : '', r.skipped && r.skipped.length ? `пропуснати: ${r.skipped.length}` : ''].filter(Boolean).join(', ')
      setMsg(`Приложено на ${r.applied} продукта${extra ? ` (${extra})` : ''}.`)
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
        <span style={{ color: 'var(--line)' }}>│</span>
        <button className="btn-paper" onClick={() => setShowPromo((s) => !s)}>🏷 Промоция за период</button>
        <button className="btn-paper" onClick={() => setShowRename((s) => !s)}>🖊 Преименувай по рецепта</button>
      </div>
      {showPromo && <PromoForm ids={ids} onDone={() => { setShowPromo(false); onDone() }} />}
      {showRename && <BulkRenameForm ids={ids} onDone={() => { setShowRename(false); onDone() }} />}
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
      {preview?.dry_run && preview.skipped && preview.skipped.length > 0 && (
        <div className="font-type" style={{ marginTop: '.5rem', fontSize: '.74rem', color: 'var(--pencil)', borderTop: '1px dashed var(--line)', paddingTop: '.4rem' }}>
          <b style={{ color: 'var(--red)' }}>{preview.skipped.length} пропуснати</b> (няма да се променят):
          {preview.skipped.slice(0, 8).map((s) => (
            <div key={s.article_id} style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '20rem' }}>{s.name} <span style={{ color: 'var(--pencil)' }}>{s.code}</span></span>
              <span>{s.reason}</span>
            </div>
          ))}
          {preview.skipped.length > 8 && <span>…и още {preview.skipped.length - 8}</span>}
        </div>
      )}
    </Sheet>
  )
}
