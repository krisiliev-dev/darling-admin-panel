import { useEffect, useState } from 'react'
import { api, ApiError } from '../lib/api'
import { useAuth } from '../lib/auth'

// Опростен регистър на марките (v2) — една плоска, редактируема структура.
// Таб „Марки“: канонично име, изписвания, префикси (100% assert), серии — редактируеми.
// Таб „Без марка“: продуктите без разпозната марка, за да се търсят начини за възстановяване.
type BrandV2 = {
  id: string; canonical: string; aliases: string[]; prefixes: string[]
  series: string[]; gs1_owner: string | null; product_count: number
}
type SummaryV2 = {
  brands: number; brands_with_prefixes: number; total_prefixes: number
  total_series: number; branded_products: number; by_tier: Record<string, number>
}
type Unbranded = {
  code: string; name: string; matched: boolean; supplier: string; supplier_name: string
  photo: boolean; prefixes: string[]; family: string; price: number | null; zero_price: boolean
}

export default function BrandRegistryV2() {
  const { me } = useAuth()
  const owner = me?.role === 'owner'
  const [tab, setTab] = useState<'brands' | 'unbranded'>('brands')
  return (
    <main style={{ padding: '1rem', maxWidth: 1050, margin: '0 auto' }}>
      <h1 style={{ fontSize: '1.2rem' }}>Марки — опростен регистър (v2)</h1>
      <div style={{ display: 'flex', gap: '.5rem', margin: '.5rem 0' }}>
        <button onClick={() => setTab('brands')} className={tab === 'brands' ? 'on' : ''}
          style={tabStyle(tab === 'brands')}>Марки</button>
        <button onClick={() => setTab('unbranded')} className={tab === 'unbranded' ? 'on' : ''}
          style={tabStyle(tab === 'unbranded')}>Без марка</button>
      </div>
      {tab === 'brands' ? <BrandsTab owner={owner} /> : <UnbrandedTab />}
    </main>
  )
}

function tabStyle(on: boolean): React.CSSProperties {
  return { padding: '.4rem .9rem', borderRadius: 8, border: '1px solid var(--line)',
    background: on ? 'var(--acc, #0f62fe)' : '#fff', color: on ? '#fff' : 'inherit',
    fontWeight: 700, cursor: 'pointer' }
}

function BrandsTab({ owner }: { owner: boolean }) {
  const [rows, setRows] = useState<BrandV2[]>([])
  const [sum, setSum] = useState<SummaryV2 | null>(null)
  const [q, setQ] = useState('')
  const [open, setOpen] = useState<string | null>(null)
  const [msg, setMsg] = useState('')

  const reload = () => {
    api.get<SummaryV2>('/api/brands-v2/summary').then(setSum).catch(() => {})
    api.get<{ brands: BrandV2[] }>('/api/brands-v2').then((d) => setRows(d.brands)).catch(() => {})
  }
  useEffect(reload, [])

  const patch = async (bid: string, body: Record<string, unknown>) => {
    setMsg('')
    try {
      await api.patch(`/api/brands-v2/${encodeURIComponent(bid)}`, body)
      reload()
    } catch (e) { setMsg(e instanceof ApiError ? e.message : 'Промяната се провали.') }
  }
  const shown = rows.filter((r) => !q || r.canonical.toLowerCase().includes(q.toLowerCase())
    || r.aliases.some((a) => a.includes(q.toLowerCase())))

  return (
    <>
      {sum && <p className="font-type" style={{ color: 'var(--pencil)', fontSize: '.85rem' }}>
        {sum.brands} марки · {sum.total_prefixes} префикса · {sum.total_series} серии ·
        {' '}{sum.branded_products} продукта с марка
        {sum.by_tier && ` (баркод ${sum.by_tier.barcode || 0}, име ${sum.by_tier.name || 0}, скрейп ${sum.by_tier.scrape || 0})`}
      </p>}
      <input placeholder="търси марка или изписване…" value={q} onChange={(e) => setQ(e.target.value)}
        style={inputStyle} />
      {msg && <p style={{ color: 'var(--red)' }}>{msg}</p>}
      {owner && <AddBrand onDone={reload} setMsg={setMsg} />}
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.85rem' }}>
        <thead><tr style={{ textAlign: 'left', borderBottom: '2px solid var(--line)' }}>
          <th style={{ padding: '.4rem' }}>Марка</th><th>Продукти</th><th>Префикси</th>
          <th>Изписвания</th><th>Серии</th>
        </tr></thead>
        <tbody>
          {shown.map((r) => (
            <>
              <tr key={r.id} style={{ borderBottom: '1px solid var(--line)', cursor: 'pointer' }}
                onClick={() => setOpen(open === r.id ? null : r.id)}>
                <td style={{ padding: '.4rem', fontWeight: 700 }}>{r.canonical}</td>
                <td style={{ color: 'var(--red)', fontWeight: 700 }}>{r.product_count}</td>
                <td>{r.prefixes.length}</td><td>{r.aliases.length}</td><td>{r.series.length}</td>
              </tr>
              {open === r.id && (
                <tr><td colSpan={5} style={{ padding: '.5rem .8rem', background: '#fafbfc' }}>
                  {r.gs1_owner && <div style={{ fontSize: '.78rem', color: 'var(--pencil)', marginBottom: '.3rem' }}>
                    GS1 собственик: {r.gs1_owner}</div>}
                  <ChipEdit label="Префикси" field="prefixes" values={r.prefixes} owner={owner}
                    onAdd={(v) => patch(r.id, { add: { prefixes: [v] } })}
                    onRemove={(v) => patch(r.id, { remove: { prefixes: [v] } })} />
                  <ChipEdit label="Изписвания" field="aliases" values={r.aliases} owner={owner}
                    onAdd={(v) => patch(r.id, { add: { aliases: [v] } })}
                    onRemove={(v) => patch(r.id, { remove: { aliases: [v] } })} />
                  <ChipEdit label="Серии" field="series" values={r.series} owner={owner}
                    onAdd={(v) => patch(r.id, { add: { series: [v] } })}
                    onRemove={(v) => patch(r.id, { remove: { series: [v] } })} />
                  {owner && <RenameDelete row={r} onPatch={patch} onDone={reload} setMsg={setMsg} />}
                </td></tr>
              )}
            </>
          ))}
        </tbody>
      </table>
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
          {x}{owner && <span onClick={() => onRemove(x)} style={{ cursor: 'pointer', color: 'var(--red)', marginLeft: 4 }}>×</span>}
        </span>
      ))}
      {values.length === 0 && <span style={{ color: '#999' }}>—</span>}
      {owner && <span style={{ marginLeft: 6 }}>
        <input value={v} onChange={(e) => setV(e.target.value)} placeholder={`+ ${field}`}
          style={{ width: 110, padding: '.15rem .35rem', fontSize: '.75rem', borderRadius: 6, border: '1px solid var(--line)' }}
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
      <input value={name} onChange={(e) => setName(e.target.value)} style={{ ...inputStyle, width: 200, margin: 0 }} />
      <button style={btn} disabled={name === row.canonical}
        onClick={() => onPatch(row.id, { canonical: name })}>Преименувай</button>
      <button style={{ ...btn, background: 'var(--red)' }}
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
      <input placeholder="id (напр. lenox)" value={id} onChange={(e) => setId(e.target.value)}
        style={{ ...inputStyle, width: 140, margin: 0 }} />
      <input placeholder="Канонично име" value={name} onChange={(e) => setName(e.target.value)}
        style={{ ...inputStyle, width: 200, margin: 0 }} />
      <button style={btn} disabled={!id.trim() || !name.trim()} onClick={async () => {
        try { await api.post('/api/brands-v2', { id: id.trim().toLowerCase(), canonical: name.trim() }); setId(''); setName(''); onDone() }
        catch (e) { setMsg(e instanceof ApiError ? e.message : 'Създаването се провали.') }
      }}>+ Нова марка</button>
    </div>
  )
}

function UnbrandedTab() {
  const [items, setItems] = useState<Unbranded[]>([])
  const [total, setTotal] = useState(0)
  const [q, setQ] = useState(''); const [onlyMatched, setOnlyMatched] = useState(false)
  const [zoom, setZoom] = useState<string | null>(null)
  const load = () => {
    const p = new URLSearchParams({ limit: '300', q, only_matched: String(onlyMatched) })
    api.get<{ total: number; items: Unbranded[] }>(`/api/brands-v2/unbranded?${p}`)
      .then((d) => { setItems(d.items); setTotal(d.total) }).catch(() => {})
  }
  useEffect(load, [onlyMatched])
  return (
    <>
      <p className="font-type" style={{ color: 'var(--pencil)', fontSize: '.85rem' }}>
        {total} продукта без разпозната марка. Подредени: първо съвпадналите (най-възстановими),
        после по баркод-фамилия — за да изпъкнат групи от една марка.</p>
      <div style={{ display: 'flex', gap: '.5rem', alignItems: 'center', margin: '.3rem 0' }}>
        <input placeholder="търси код / име / доставчик…" value={q}
          onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && load()}
          style={{ ...inputStyle, flex: 1, margin: 0 }} />
        <button style={btn} onClick={load}>Търси</button>
        <label style={{ fontSize: '.8rem' }}>
          <input type="checkbox" checked={onlyMatched} onChange={(e) => setOnlyMatched(e.target.checked)} /> само съвпаднали
        </label>
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.8rem' }}>
        <thead><tr style={{ textAlign: 'left', borderBottom: '2px solid var(--line)' }}>
          <th></th><th style={{ padding: '.3rem' }}>Код</th><th>Име</th><th>Доставчик</th>
          <th>Фамилия</th><th>Цена</th>
        </tr></thead>
        <tbody>
          {items.map((r) => (
            <tr key={r.code} style={{ borderBottom: '1px solid var(--line)' }}>
              <td style={{ width: 46 }}>
                {r.photo
                  ? <img src={`/api/brand-registry/photo/${encodeURIComponent(r.code)}`} alt=""
                      onClick={() => setZoom(`/api/brand-registry/photo/${encodeURIComponent(r.code)}`)}
                      style={{ width: 40, height: 40, objectFit: 'contain', cursor: 'zoom-in' }} />
                  : <span style={{ color: '#ccc' }}>—</span>}
              </td>
              <td style={{ padding: '.3rem', fontFamily: 'monospace' }}>{r.code}</td>
              <td>{r.name}{r.matched && <span title="съвпаднал" style={{ color: '#1a9d5a' }}> ✓</span>}
                {r.supplier_name && <div style={{ color: '#999', fontSize: '.72rem' }}>{r.supplier_name}</div>}</td>
              <td>{r.supplier || '—'}</td>
              <td style={{ fontFamily: 'monospace', color: 'var(--pencil)' }}>{r.family || '—'}</td>
              <td style={{ color: r.zero_price ? 'var(--red)' : 'inherit' }}>{r.price ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {zoom && <div onClick={() => setZoom(null)} style={{ position: 'fixed', inset: 0, background: '#000a',
        display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 20, cursor: 'zoom-out' }}>
        <img src={zoom} alt="" style={{ maxWidth: '90%', maxHeight: '90%', background: '#fff' }} /></div>}
    </>
  )
}

const inputStyle: React.CSSProperties = { width: '100%', padding: '.5rem', margin: '.4rem 0',
  borderRadius: 8, border: '1px solid var(--line)' }
const chip: React.CSSProperties = { background: '#eef2f7', borderRadius: 14, padding: '.1rem .5rem', marginRight: 4, display: 'inline-block' }
const btn: React.CSSProperties = { background: 'var(--acc, #0f62fe)', color: '#fff', border: 0,
  borderRadius: 8, padding: '.4rem .8rem', fontWeight: 700, cursor: 'pointer' }
