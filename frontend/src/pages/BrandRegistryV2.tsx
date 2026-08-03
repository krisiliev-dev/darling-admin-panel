import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, ApiError } from '../lib/api'
import { useAuth } from '../lib/auth'

// Опростен регистър на марките (v2) — една плоска, редактируема структура.
// „Марки“: канонично име, изписвания, префикси (100% assert), серии — редактируеми.
// „Без марка“: продуктите без разпозната марка, за да се търсят начини за възстановяване.
//
// Both tabs are EXPORTED because they are now rendered inside Продукти → Брандове (BrandsHub);
// this file's default export only serves the /marki-v2/:brandId detail route.
type BrandV2 = {
  id: string; canonical: string; aliases: string[]; prefixes: string[]
  series: string[]; gs1_owner: string | null; product_count: number; edited?: boolean
}
type SummaryV2 = {
  brands: number; brands_with_prefixes: number; total_prefixes: number
  total_series: number; branded_products: number; by_tier: Record<string, number>
  owner_edited?: number; deleted?: number
}
type Unbranded = {
  code: string; name: string; matched: boolean; supplier: string; supplier_name: string
  photo: boolean; prefixes: string[]; family: string; price: number | null; zero_price: boolean
}

export default function BrandRegistryV2() {
  const { me } = useAuth()
  const owner = me?.role === 'owner'
  const { brandId } = useParams()
  if (brandId) return (
    <div className="sheet" style={{ padding: '1rem 1.2rem' }}>
      <BrandDetailV2 id={brandId} owner={owner} />
    </div>
  )
  return <BrandsTab owner={owner} />
}

export function BrandsTab({ owner }: { owner: boolean }) {
  const [rows, setRows] = useState<BrandV2[]>([])
  const [sum, setSum] = useState<SummaryV2 | null>(null)
  const [q, setQ] = useState('')
  const [msg, setMsg] = useState('')
  const navigate = useNavigate()

  const reload = () => {
    api.get<SummaryV2>('/api/brands-v2/summary').then(setSum).catch(() => {})
    // a list endpoint that answers with a surprise gives us an EMPTY list, never a crash:
    // rows.filter() on a non-array takes the whole tab into the ErrorBoundary
    api.get<{ brands: BrandV2[] }>('/api/brands-v2')
      .then((d) => setRows(Array.isArray(d?.brands) ? d.brands : []))
      .catch(() => setRows([]))
  }
  useEffect(reload, [])

  const shown = rows.filter((r) => !q || r.canonical.toLowerCase().includes(q.toLowerCase())
    || r.aliases.some((a) => a.includes(q.toLowerCase())))

  return (
    <>
      {sum && <p className="font-type" style={{ color: 'var(--pencil)', fontSize: '.85rem' }}>
        {sum.brands} марки · {sum.total_prefixes} префикса · {sum.total_series} серии ·
        {' '}{sum.branded_products} продукта с марка
        {sum.by_tier && ` (баркод ${sum.by_tier.barcode || 0}, име ${sum.by_tier.name || 0}, скрейп ${sum.by_tier.scrape || 0})`}
        {typeof sum.owner_edited === 'number' && sum.owner_edited > 0 &&
          ` · ${sum.owner_edited} редактирани ръчно (не се пипат от пайплайна)`}
      </p>}
      <input className="input-paper" placeholder="търси марка или изписване…" value={q}
        onChange={(e) => setQ(e.target.value)} style={{ margin: '.4rem 0' }} />
      {msg && <p className="msg msg--error" role="alert">{msg}</p>}
      {owner && <AddBrand onDone={reload} setMsg={setMsg} />}
      <table className="table-paper font-type">
        <thead><tr>
          <th scope="col">Марка</th><th scope="col" className="num">Продукти</th>
          <th scope="col" className="num">Префикси</th><th scope="col" className="num">Изписвания</th>
          <th scope="col" className="num">Серии</th><th scope="col">Източник</th>
        </tr></thead>
        <tbody>
          {shown.map((r) => (
            <tr key={r.id} style={{ cursor: 'pointer' }} tabIndex={0}
              onClick={() => navigate(`/marki-v2/${encodeURIComponent(r.id)}`)}
              onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigate(`/marki-v2/${encodeURIComponent(r.id)}`) } }}>
              <td style={{ fontWeight: 700 }}>{r.canonical}</td>
              <td className="num" style={{ color: 'var(--red)', fontWeight: 700 }}>{r.product_count}</td>
              <td className="num">{r.prefixes.length}</td>
              <td className="num">{r.aliases.length}</td>
              <td className="num">{r.series.length}</td>
              <td style={{ color: 'var(--pencil)', fontSize: '.72rem' }}>
                {r.edited ? 'ръчно' : 'пайплайн'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {shown.length === 0 && <p className="pencil" style={{ fontSize: '.8rem' }}>Няма намерени марки.</p>}
    </>
  )
}

type ProdCard = { code: string; name: string | null; photo: string | null; series?: string }

function BrandDetailV2({ id, owner }: { id: string; owner: boolean }) {
  const navigate = useNavigate()
  const [b, setB] = useState<BrandV2 | null>(null)
  const [prods, setProds] = useState<{ total: number; catalog_available?: boolean; items: ProdCard[] }>({ total: 0, items: [] })
  const [msg, setMsg] = useState('')
  const reload = () => {
    api.get<BrandV2>(`/api/brands-v2/${encodeURIComponent(id)}`).then(setB).catch(() => {})
    api.get<{ total: number; catalog_available?: boolean; items: ProdCard[] }>(`/api/brands-v2/${encodeURIComponent(id)}/products?limit=600`).then(setProds).catch(() => {})
  }
  useEffect(reload, [id])
  const patch = async (bid: string, body: Record<string, unknown>) => {
    setMsg('')
    try { await api.patch(`/api/brands-v2/${encodeURIComponent(bid)}`, body); reload() }
    catch (e) { setMsg(e instanceof ApiError ? e.message : 'Промяната се провали.') }
  }
  if (!b) return <p className="font-type" style={{ color: 'var(--pencil)' }}>Зареждане…</p>
  return (
    <>
      <Link to="/products/brands" style={{ fontSize: '.85rem', color: 'var(--red)' }}>← Всички марки</Link>
      <h1 style={{ fontSize: '1.3rem', margin: '.3rem 0' }}>{b.canonical}</h1>
      <p className="font-type" style={{ color: 'var(--pencil)', fontSize: '.85rem' }}>
        {b.product_count} продукта · {b.prefixes.length} префикса · {b.aliases.length} изписвания · {b.series.length} серии</p>
      {b.gs1_owner && <div style={{ fontSize: '.78rem', color: 'var(--pencil)', marginBottom: '.3rem' }}>GS1 собственик: {b.gs1_owner}</div>}
      <ChipEdit label="Префикси" field="prefixes" values={b.prefixes} owner={owner}
        onAdd={(v) => patch(b.id, { add: { prefixes: [v] } })} onRemove={(v) => patch(b.id, { remove: { prefixes: [v] } })} />
      <ChipEdit label="Изписвания" field="aliases" values={b.aliases} owner={owner}
        onAdd={(v) => patch(b.id, { add: { aliases: [v] } })} onRemove={(v) => patch(b.id, { remove: { aliases: [v] } })} />
      <ChipEdit label="Серии" field="series" values={b.series} owner={owner}
        onAdd={(v) => patch(b.id, { add: { series: [v] } })} onRemove={(v) => patch(b.id, { remove: { series: [v] } })} />
      {owner && <RenameDelete row={b} onPatch={patch} onDone={() => navigate('/products/brands')} setMsg={setMsg} />}
      {msg && <p className="msg msg--error" role="alert">{msg}</p>}
      <h2 style={{ fontSize: '1rem', margin: '.9rem 0 .4rem' }}>Продукти ({prods.total})</h2>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: '.6rem' }}>
        {prods.items.map((p) => (
          <div key={p.code} style={{ border: '1px solid var(--line)', borderRadius: 2, padding: '.4rem', background: 'var(--paper)' }}>
            <div style={{ height: 110, display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
              {p.photo
                ? <img src={p.photo} alt="" style={{ maxWidth: '100%', maxHeight: 110, objectFit: 'contain' }} />
                : <span style={{ color: 'var(--line)', fontSize: '.75rem' }}>няма снимка</span>}
            </div>
            <div style={{ fontSize: '.78rem', marginTop: '.3rem', lineHeight: 1.25 }}>{p.name || p.code}</div>
            <div style={{ fontFamily: 'monospace', fontSize: '.7rem', color: 'var(--pencil)' }}>
              {p.code}{p.series ? ` · ${p.series}` : ''}</div>
          </div>
        ))}
        {prods.items.length === 0 && <span style={{ color: 'var(--pencil)' }}>—</span>}
      </div>
    </>
  )
}

function ChipEdit({ label, field, values, owner, onAdd, onRemove }: {
  label: string; field: string; values: string[]; owner: boolean
  onAdd: (v: string) => void; onRemove: (v: string) => void
}) {
  const [v, setV] = useState('')
  return (
    <div style={{ margin: '.25rem 0', fontSize: '.8rem' }}>
      <b>{label}:</b>{' '}
      {values.map((x) => (
        <span key={x} style={chip}>
          {x}{owner && <button aria-label={`премахни ${x}`} onClick={() => onRemove(x)}
            style={{ cursor: 'pointer', color: 'var(--red)', marginLeft: 4, border: 0, background: 'transparent' }}>×</button>}
        </span>
      ))}
      {values.length === 0 && <span style={{ color: 'var(--pencil)' }}>—</span>}
      {owner && <span style={{ marginLeft: 6 }}>
        <input value={v} onChange={(e) => setV(e.target.value)} placeholder={`+ ${field}`}
          aria-label={`добави ${label.toLowerCase()}`}
          style={{ width: 110, padding: '.15rem .35rem', fontSize: '.75rem', borderRadius: 2, border: '1px solid var(--line)', background: 'var(--field, #fff)', color: 'var(--ink)' }}
          onKeyDown={(e) => { if (e.key === 'Enter' && v.trim()) { onAdd(v.trim()); setV('') } }} />
      </span>}
    </div>
  )
}

function RenameDelete({ row, onPatch, onDone, setMsg }: {
  row: BrandV2; onPatch: (bid: string, b: Record<string, unknown>) => void
  onDone: () => void; setMsg: (s: string) => void
}) {
  const [name, setName] = useState(row.canonical)
  return (
    <div style={{ marginTop: '.4rem', display: 'flex', gap: '.4rem', alignItems: 'center' }}>
      <input className="input-paper" value={name} onChange={(e) => setName(e.target.value)}
        aria-label="канонично име" style={{ width: 200 }} />
      <button className="btn-paper" disabled={name === row.canonical}
        onClick={() => onPatch(row.id, { canonical: name })}>Преименувай</button>
      <button className="btn-paper btn-red"
        onClick={async () => {
          if (!confirm(`Изтрий марката „${row.canonical}“?`)) return
          try { await api.del(`/api/brands-v2/${encodeURIComponent(row.id)}`); onDone() }
          catch (e) { setMsg(e instanceof ApiError ? e.message : 'Изтриването се провали.') }
        }}>Изтрий</button>
    </div>
  )
}

function AddBrand({ onDone, setMsg }: { onDone: () => void; setMsg: (s: string) => void }) {
  const [id, setId] = useState(''); const [name, setName] = useState('')
  return (
    <div style={{ display: 'flex', gap: '.4rem', margin: '.4rem 0', alignItems: 'center' }}>
      <input className="input-paper" placeholder="id (напр. lenox)" value={id}
        aria-label="id на марката" onChange={(e) => setId(e.target.value)} style={{ width: 140 }} />
      <input className="input-paper" placeholder="Канонично име" value={name}
        aria-label="канонично име" onChange={(e) => setName(e.target.value)} style={{ width: 200 }} />
      <button className="btn-paper btn-red" disabled={!id.trim() || !name.trim()} onClick={async () => {
        try { await api.post('/api/brands-v2', { id: id.trim().toLowerCase(), canonical: name.trim() }); setId(''); setName(''); onDone() }
        catch (e) { setMsg(e instanceof ApiError ? e.message : 'Създаването се провали.') }
      }}>+ Нова марка</button>
    </div>
  )
}

export function UnbrandedTab() {
  const PAGE = 200
  const [items, setItems] = useState<Unbranded[]>([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [q, setQ] = useState(''); const [onlyMatched, setOnlyMatched] = useState(false)
  const [zoom, setZoom] = useState<string | null>(null)
  const load = (off = offset) => {
    const p = new URLSearchParams({ limit: String(PAGE), offset: String(off), q, only_matched: String(onlyMatched) })
    api.get<{ total: number; items: Unbranded[] }>(`/api/brands-v2/unbranded?${p}`)
      .then((d) => { setItems(Array.isArray(d?.items) ? d.items : []); setTotal(d?.total || 0) })
      .catch(() => { setItems([]); setTotal(0) })
  }
  useEffect(() => { load(offset) }, [onlyMatched, offset])
  const search = () => { setOffset(0); load(0) }
  return (
    <>
      <p className="font-type" style={{ color: 'var(--pencil)', fontSize: '.85rem' }}>
        {total} продукта без разпозната марка. Подредени: първо съвпадналите (най-възстановими),
        после по баркод-фамилия — за да изпъкнат групи от една марка.</p>
      <div style={{ display: 'flex', gap: '.5rem', alignItems: 'center', margin: '.3rem 0' }}>
        <input className="input-paper" type="search" placeholder="търси код / име / доставчик…" value={q}
          onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && search()}
          style={{ flex: 1 }} />
        <button className="btn-paper" onClick={search}>Търси</button>
        <label style={{ fontSize: '.8rem', whiteSpace: 'nowrap' }}>
          <input type="checkbox" checked={onlyMatched} onChange={(e) => { setOnlyMatched(e.target.checked); setOffset(0) }} /> само съвпаднали
        </label>
      </div>
      <table className="table-paper font-type">
        <thead><tr>
          <th scope="col"></th><th scope="col">Код</th><th scope="col">Име</th>
          <th scope="col">Доставчик</th><th scope="col">Фамилия</th><th scope="col" className="num">Цена</th>
        </tr></thead>
        <tbody>
          {items.map((r) => (
            <tr key={r.code}>
              <td style={{ width: 46 }}>
                {r.photo
                  ? <img src={`/api/brand-registry/photo/${encodeURIComponent(r.code)}`} alt=""
                      onClick={() => setZoom(`/api/brand-registry/photo/${encodeURIComponent(r.code)}`)}
                      style={{ width: 40, height: 40, objectFit: 'contain', cursor: 'zoom-in' }} />
                  : <span style={{ color: 'var(--line)' }}>—</span>}
              </td>
              <td style={{ fontFamily: 'monospace' }}>{r.code}</td>
              <td>{r.name}{r.matched && <span title="съвпаднал" style={{ color: 'var(--ready, #2f6b3a)' }}> ✓</span>}
                {r.supplier_name && <div style={{ color: 'var(--pencil)', fontSize: '.72rem' }}>{r.supplier_name}</div>}</td>
              <td>{r.supplier || '—'}</td>
              <td style={{ fontFamily: 'monospace', color: 'var(--pencil)' }}>{r.family || '—'}</td>
              <td className="num" style={{ color: r.zero_price ? 'var(--red)' : 'inherit' }}>{r.price ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ display: 'flex', gap: '.6rem', alignItems: 'center', justifyContent: 'center', margin: '.6rem 0' }}>
        <button className="btn-paper" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>← Назад</button>
        <span style={{ fontSize: '.8rem', color: 'var(--pencil)' }}>
          {total === 0 ? '0' : `${offset + 1}–${Math.min(offset + PAGE, total)}`} от {total}</span>
        <button className="btn-paper" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>Напред →</button>
      </div>
      {zoom && <div onClick={() => setZoom(null)} style={{ position: 'fixed', inset: 0, background: '#000a',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 20, cursor: 'zoom-out' }}>
        <img src={zoom} alt="" style={{ maxWidth: '90%', maxHeight: '90%', background: '#fff' }} /></div>}
    </>
  )
}

const chip: React.CSSProperties = { background: 'var(--paper2)', border: '1px solid var(--line)',
  borderRadius: 3, padding: '.1rem .5rem', marginRight: 4, display: 'inline-block' }
