import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { ArticleDetail, CacheFacets, CacheProduct, CacheSearchResp, QualityAudit } from '../lib/api'
import { useHotkey, isTyping } from '../lib/useHotkey'
import { Sheet } from '../components/Paper'
import { PageHeader, EmptyNote, Loading } from '../components/PageHeader'
import { BulkBar } from '../components/ProductBulkPricing'

const PER = 100

// Каталог — browse + edit products from Barsy (live STOCK folded in). Runs off the local product
// cache so filtering by name/код/доставчик/категория/цена/атрибут is instant. Price is read-only.
export function Catalog({ embedded = false }: { embedded?: boolean } = {}) {
  const [facets, setFacets] = useState<CacheFacets | null>(null)
  const [q, setQ] = useState('')
  const [supplier, setSupplier] = useState('')
  const [category, setCategory] = useState('')
  const [priceMin, setPriceMin] = useState('')
  const [priceMax, setPriceMax] = useState('')
  const [lowOnly, setLowOnly] = useState(false)
  const [attrs, setAttrs] = useState<Record<string, string>>({})
  const [brand, setBrand] = useState('')
  const [desc, setDesc] = useState('')
  const [stockMin, setStockMin] = useState('')
  const [stockMax, setStockMax] = useState('')
  const [flags, setFlags] = useState<Record<string, string>>({})   // has_image/has_barcode/has_category/has_price → ''|'true'|'false'
  const [sort, setSort] = useState('name')
  const [order, setOrder] = useState('asc')
  const [showAdv, setShowAdv] = useState(false)
  const [page, setPage] = useState(1)
  const [data, setData] = useState<CacheSearchResp | null>(null)
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<number | null>(null)
  const [sel, setSel] = useState<Map<number, CacheProduct>>(new Map())   // selection persists across pages
  const searchRef = useRef<HTMLInputElement>(null)
  useHotkey((e) => e.key === '/' && !isTyping(e), () => searchRef.current?.focus())
  const seqRef = useRef(0)
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  useEffect(() => { api.get<CacheFacets>('/api/catalog/facets').then(setFacets).catch(() => setFacets({ suppliers: [], categories: [], attributes: {} })) }, [])

  const query = useMemo(() => {
    const p = new URLSearchParams()
    if (q.trim()) p.set('q', q.trim())
    if (supplier) p.set('supplier', supplier)
    if (category) p.set('category', category)
    if (brand) p.set('brand', brand)
    if (desc.trim()) p.set('desc', desc.trim())
    if (priceMin) p.set('price_min', priceMin)
    if (priceMax) p.set('price_max', priceMax)
    if (stockMin) p.set('stock_min', stockMin)
    if (stockMax) p.set('stock_max', stockMax)
    if (lowOnly) p.set('low_only', 'true')
    Object.entries(flags).forEach(([k, v]) => { if (v) p.set(k, v) })
    Object.entries(attrs).forEach(([k, v]) => { if (v) p.append('attr', `${k}=${v}`) })
    p.set('sort', sort); p.set('order', order)
    p.set('per_page', String(PER)); p.set('page', String(page))
    return p.toString()
  }, [q, supplier, category, brand, desc, priceMin, priceMax, stockMin, stockMax, lowOnly, flags, attrs, sort, order, page])

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
  useEffect(() => { setPage(1) }, [q, supplier, category, brand, desc, priceMin, priceMax, stockMin, stockMax, lowOnly, flags, attrs, sort, order])

  const items = data?.items || []
  const total = data?.total || 0
  const pages = Math.max(1, Math.ceil(total / PER))
  const toggle = (p: CacheProduct) => setSel((m) => { const n = new Map(m); n.has(p.article_id) ? n.delete(p.article_id) : n.set(p.article_id, p); return n })
  const pageAllSel = items.length > 0 && items.every((i) => sel.has(i.article_id))
  const togglePage = () => setSel((m) => {
    const n = new Map(m)
    if (items.every((i) => n.has(i.article_id))) items.forEach((i) => n.delete(i.article_id))
    else items.forEach((i) => n.set(i.article_id, i))
    return n
  })

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      {!embedded && <PageHeader title="Каталог" subtitle="продукти + наличност от Barsy" />}

      <AuditPanel onOpen={setSelected} />

      <div style={{ display: 'grid', gridTemplateColumns: selected ? '1fr 1.2fr' : '1fr', gap: '1.1rem' }}>
        <Sheet clip="left">
          {/* filter bar */}
          <div style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap', alignItems: 'center' }}>
            <input ref={searchRef} className="input-paper" style={{ width: '13rem' }} placeholder="търси име / код  ( / )" value={q} onChange={(e) => setQ(e.target.value)} />
            <select className="input-paper" value={supplier} onChange={(e) => setSupplier(e.target.value)}>
              <option value="">— доставчик —</option>
              {facets?.suppliers.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <select className="input-paper" value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">— категория —</option>
              {facets?.categories.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
            {facets?.brands && facets.brands.length > 0 && (
              <select className="input-paper" value={brand} onChange={(e) => setBrand(e.target.value)}>
                <option value="">— бранд —</option>
                {facets.brands.map((b) => <option key={b} value={b}>{b}</option>)}
              </select>
            )}
            <input className="input-paper" style={{ width: '6rem' }} inputMode="decimal" placeholder="цена ≥" value={priceMin} onChange={(e) => setPriceMin(e.target.value)} />
            <input className="input-paper" style={{ width: '6rem' }} inputMode="decimal" placeholder="цена ≤" value={priceMax} onChange={(e) => setPriceMax(e.target.value)} />
            <label className="font-type" style={{ fontSize: '.74rem', color: 'var(--pencil)', display: 'flex', gap: '.3rem', alignItems: 'center', cursor: 'pointer' }}>
              <input type="checkbox" checked={lowOnly} onChange={(e) => setLowOnly(e.target.checked)} /> ниска налич.
            </label>
            <button className="btn-paper" style={{ fontSize: '.72rem' }} onClick={() => setShowAdv((s) => !s)}>{showAdv ? 'по-малко филтри ▴' : 'още филтри ▾'}</button>
          </div>
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
          {showAdv && (
            <div style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap', marginTop: '.5rem', alignItems: 'center', borderTop: '1px dashed var(--line)', paddingTop: '.5rem' }}>
              <input className="input-paper" style={{ width: '13rem' }} placeholder="дума в описанието…" value={desc} onChange={(e) => setDesc(e.target.value)} />
              <input className="input-paper" style={{ width: '6rem' }} inputMode="numeric" placeholder="налич. ≥" value={stockMin} onChange={(e) => setStockMin(e.target.value)} />
              <input className="input-paper" style={{ width: '6rem' }} inputMode="numeric" placeholder="налич. ≤" value={stockMax} onChange={(e) => setStockMax(e.target.value)} />
              {([['has_image', 'снимка'], ['has_barcode', 'баркод'], ['has_category', 'категория'], ['has_price', 'цена']] as [string, string][]).map(([k, lbl]) => (
                <select key={k} className="input-paper" style={{ fontSize: '.72rem' }} value={flags[k] || ''} onChange={(e) => setFlags((f) => ({ ...f, [k]: e.target.value }))}>
                  <option value="">{lbl}: всички</option>
                  <option value="true">с {lbl}</option>
                  <option value="false">без {lbl}</option>
                </select>
              ))}
              <span className="font-type" style={{ fontSize: '.72rem', color: 'var(--pencil)' }}>подредба:</span>
              <select className="input-paper" style={{ fontSize: '.72rem' }} value={sort} onChange={(e) => setSort(e.target.value)}>
                {([['name', 'име'], ['code', 'код'], ['price', 'цена'], ['stock', 'наличност'], ['supplier', 'доставчик'], ['brand', 'бранд']] as [string, string][]).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
              </select>
              <select className="input-paper" style={{ fontSize: '.72rem' }} value={order} onChange={(e) => setOrder(e.target.value)}>
                <option value="asc">възходящо</option><option value="desc">низходящо</option>
              </select>
              <button className="btn-paper" style={{ fontSize: '.72rem' }} onClick={() => { setQ(''); setSupplier(''); setCategory(''); setBrand(''); setDesc(''); setPriceMin(''); setPriceMax(''); setStockMin(''); setStockMax(''); setLowOnly(false); setFlags({}); setAttrs({}); setSort('name'); setOrder('asc') }}>изчисти филтрите</button>
            </div>
          )}
          <div className="font-type" style={{ fontSize: '.74rem', color: 'var(--pencil)', margin: '.5rem 0', display: 'flex', gap: '.7rem', alignItems: 'center', flexWrap: 'wrap' }}>
            <span>{data ? `${total} продукта` : ''}</span>
            {sel.size > 0 && <span style={{ color: 'var(--red)' }}>· {sel.size} избрани <button className="btn-paper" style={{ fontSize: '.68rem', padding: '.05rem .3rem' }} onClick={() => setSel(new Map())}>изчисти</button></span>}
          </div>

          {sel.size > 0 && <div style={{ marginBottom: '.6rem' }}><BulkBar selected={[...sel.values()]} onDone={() => { setSel(new Map()); load() }} /></div>}

          {loading && !data ? <Loading /> : items.length === 0 ? (
            <EmptyNote>{q || supplier || category || lowOnly ? 'Няма продукти за този филтър.' : 'Каталогът се пълни, щом одобрени продукти се качат в Barsy.'}</EmptyNote>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table className="font-type" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.8rem' }}>
                <thead><tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--red)', textAlign: 'left' }}>
                  <th style={{ padding: '.3rem' }}><input type="checkbox" checked={pageAllSel} onChange={togglePage} title="избери страницата" /></th>
                  <th style={{ padding: '.3rem' }}></th><th style={{ padding: '.3rem' }}>ID</th>
                  <th style={{ padding: '.3rem' }}>Продукт</th><th style={{ padding: '.3rem' }}>Доставчик</th>
                  <th style={{ padding: '.3rem', textAlign: 'right' }}>Наличност</th><th style={{ padding: '.3rem', textAlign: 'right' }}>Цена</th>
                </tr></thead>
                <tbody>
                  {items.map((it: CacheProduct) => (
                    <tr key={it.article_id} onClick={() => setSelected(it.article_id)} style={{
                      cursor: 'pointer', borderBottom: '1px dashed var(--line)',
                      background: sel.has(it.article_id) ? 'var(--paper)' : selected === it.article_id ? 'var(--paper2)' : 'transparent',
                    }}>
                      <td style={{ padding: '.3rem' }} onClick={(e) => e.stopPropagation()}>
                        <input type="checkbox" checked={sel.has(it.article_id)} onChange={() => toggle(it)} />
                      </td>
                      <td style={{ padding: '.3rem' }}><Thumb articleId={it.article_id} hasImage={it.has_image} /></td>
                      <td style={{ padding: '.3rem', color: 'var(--pencil)', fontVariantNumeric: 'tabular-nums' }}>#{it.article_id}</td>
                      <td style={{ padding: '.3rem' }}>{it.name || '(без име)'} <span style={{ color: 'var(--pencil)' }}>{it.code}</span></td>
                      <td style={{ padding: '.3rem', color: 'var(--pencil)' }}>{it.supplier || '—'}</td>
                      <td style={{ padding: '.3rem', textAlign: 'right', color: it.low ? 'var(--blocked)' : 'var(--pencil)', whiteSpace: 'nowrap' }}>{it.stock != null ? `${it.stock} бр${it.low ? ' ⚠' : ''}` : '—'}</td>
                      <td style={{ padding: '.3rem', textAlign: 'right', color: 'var(--pencil)', whiteSpace: 'nowrap' }}>{it.price != null ? `${it.price} €` : ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {pages > 1 && (
                <div style={{ display: 'flex', gap: '.5rem', alignItems: 'center', justifyContent: 'center', marginTop: '.5rem' }}>
                  <button className="btn-paper" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>←</button>
                  <span className="font-type" style={{ fontSize: '.75rem', color: 'var(--pencil)' }}>стр. {page} / {pages}</span>
                  <button className="btn-paper" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>→</button>
                </div>
              )}
            </div>
          )}
        </Sheet>

        {selected && <ArticleEditor articleId={selected} onClose={() => setSelected(null)} onSaved={load} />}
      </div>
    </div>
  )
}

function ArticleEditor({ articleId, onClose, onSaved }: { articleId: number; onClose: () => void; onSaved: () => void }) {
  const [detail, setDetail] = useState<ArticleDetail | null>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [barcode, setBarcode] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  useEffect(() => {
    let ignore = false  // ignore a stale response if the user clicks another article first
    setDetail(null); setMsg('')
    api.get<ArticleDetail>(`/api/catalog/${articleId}`).then((d) => {
      if (ignore) return
      setDetail(d); setName(d.name); setDescription(d.description); setBarcode(d.barcode)
    }).catch(() => { if (!ignore) setMsg('Грешка при зареждане.') })
    return () => { ignore = true }
  }, [articleId])

  const save = async () => {
    setBusy(true); setMsg('')
    try {
      await api.post(`/api/catalog/${articleId}`, { name, description, barcode })
      setMsg('Записано в Barsy.')
      onSaved()
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : 'Записът се провали.')
    } finally { setBusy(false) }
  }

  return (
    <Sheet clip="right" tilt="r">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '.5rem' }}>
        <h3 style={{ fontSize: '1rem' }}>Редакция</h3>
        <button onClick={onClose} style={{ border: 'none', background: 'transparent', cursor: 'pointer', color: 'var(--pencil)', fontFamily: 'var(--type)', fontSize: '.75rem' }}>затвори ✕</button>
      </div>
      {!detail ? <Loading /> : (
        <div style={{ display: 'grid', gap: '.5rem' }}>
          <div style={{ display: 'flex', gap: '.7rem', alignItems: 'flex-start' }}>
            <Thumb articleId={articleId} size={72} />
            <div className="font-type" style={{ fontSize: '.72rem', color: 'var(--pencil)' }}>
              код {detail.code} · {detail.price != null ? `${detail.price} € (от Microinvest — не се редактира)` : 'без цена'}{detail.category ? ` · ${detail.category}` : ''}
            </div>
          </div>
          <Field label="Име"><input className="input-paper" value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="Баркод"><input className="input-paper" value={barcode} onChange={(e) => setBarcode(e.target.value)} /></Field>
          <Field label="Описание (HTML)">
            <textarea className="input-paper" rows={10} value={description} onChange={(e) => setDescription(e.target.value)} style={{ fontSize: '.78rem', fontFamily: 'var(--serif)' }} />
          </Field>
          <div style={{ display: 'flex', gap: '.6rem', alignItems: 'center' }}>
            <button className="btn-paper btn-red" onClick={() => void save()} disabled={busy}>{busy ? 'записва…' : 'Запиши в Barsy'}</button>
            {msg && <span className="font-type" style={{ fontSize: '.8rem', color: 'var(--pencil)' }}>{msg}</span>}
          </div>
        </div>
      )}
    </Sheet>
  )
}

// a small product thumbnail via the auth-proxy; falls back to a paper placeholder on 404/no-image
function Thumb({ articleId, hasImage, size = 34 }: { articleId: number; hasImage?: boolean; size?: number }) {
  const [failed, setFailed] = useState(false)
  const box: React.CSSProperties = { width: size, height: size, borderRadius: 3, border: '1px solid var(--line)', flexShrink: 0, objectFit: 'cover' }
  if (hasImage === false || failed)
    return <span aria-hidden style={{ ...box, background: 'var(--manila)', display: 'inline-grid', placeItems: 'center', color: 'var(--pencil)', fontSize: size * 0.4 }}>▦</span>
  return <img src={`/api/catalog/image/${articleId}?size=thumb`} alt="" loading="lazy" style={box} onError={() => setFailed(true)} />
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'grid', gap: '.2rem' }}>
      <span className="font-type" style={{ fontSize: '.62rem', color: 'var(--red)', textTransform: 'uppercase', letterSpacing: '.06em' }}>{label}</span>
      {children}
    </label>
  )
}

// Whole-catalog data-quality audit — a second automated net on top of the owner's manual audit.
// Loads on demand (it scans the Barsy catalog). Click a flagged product to open it in the editor.
function AuditPanel({ onOpen }: { onOpen: (id: number) => void }) {
  const [rep, setRep] = useState<QualityAudit | null>(null)
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)

  const run = async () => {
    setBusy(true)
    try { setRep(await api.get<QualityAudit>('/api/quality/audit')); setOpen(true) }
    catch { /* surfaced by empty */ } finally { setBusy(false) }
  }

  return (
    <Sheet>
      <div style={{ display: 'flex', gap: '.6rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <h3 style={{ fontSize: '1rem', flex: 1 }}>Одит на качеството</h3>
        {rep && <button className="btn-paper" style={{ fontSize: '.72rem' }} onClick={() => setOpen((o) => !o)}>{open ? 'скрий' : 'покажи'}</button>}
        <button className="btn-paper btn-red" onClick={() => void run()} disabled={busy}>{busy ? 'проверява…' : rep ? 'Провери отново' : 'Провери каталога'}</button>
      </div>
      {rep && open && (
        <div style={{ marginTop: '.7rem', display: 'grid', gap: '.6rem' }}>
          <div className="font-type" style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', fontSize: '.8rem' }}>
            <span>Проверени: <b>{rep.total}</b>{rep.scan_truncated ? '+' : ''}</span>
            <span style={{ color: rep.flagged_count ? 'var(--pending)' : 'var(--ready)' }}>● Проблемни: <b>{rep.flagged_count}</b></span>
            <span style={{ color: 'var(--ready)' }}>● Чисти: <b>{rep.clean_count}</b></span>
            {rep.duplicate_barcodes > 0 && <span style={{ color: 'var(--red)' }}>дублирани баркодове: {rep.duplicate_barcodes}</span>}
            {rep.duplicate_names > 0 && <span style={{ color: 'var(--pending)' }}>възможни дубликати (име): {rep.duplicate_names}</span>}
          </div>
          {rep.flagged.length === 0 ? (
            <p className="font-type" style={{ fontSize: '.82rem', color: 'var(--ready)' }}>Всичко е наред — няма проблемни продукти.</p>
          ) : (
            <div style={{ display: 'grid', gap: '.25rem', maxHeight: '22rem', overflowY: 'auto' }}>
              {rep.flagged.map((f) => (
                <button key={f.article_id} onClick={() => onOpen(f.article_id)}
                  style={{ display: 'grid', gap: '.2rem', textAlign: 'left', border: 'none', background: 'transparent', cursor: 'pointer', padding: '.3rem .4rem', borderLeft: '3px solid var(--pending)', borderBottom: '1px dashed var(--line)' }}>
                  <span style={{ fontSize: '.82rem' }}>{f.name || '(без име)'} <span style={{ color: 'var(--pencil)' }}>{f.code}</span></span>
                  <span style={{ display: 'flex', gap: '.3rem', flexWrap: 'wrap' }}>
                    {f.issues.map((i, k) => <span key={k} className="font-type" style={{ fontSize: '.62rem', color: 'var(--red-ink)', border: '1px solid var(--line)', borderRadius: 2, padding: '.05rem .3rem' }}>{i}</span>)}
                  </span>
                </button>
              ))}
              {rep.flagged_returned < rep.flagged_count && (
                <p className="pencil" style={{ fontSize: '.74rem' }}>…показани са първите {rep.flagged_returned} от {rep.flagged_count}.</p>
              )}
            </div>
          )}
        </div>
      )}
    </Sheet>
  )
}
