import { useEffect, useState } from 'react'
import { api, ApiError } from '../lib/api'

// Опростен регистър на марките (v2) — една плоска структура: марка → канонично име,
// изписвания (aliases), префикси (100% assert), серии. Read-only изглед.
type BrandV2 = {
  id: string; canonical: string; aliases: string[]; prefixes: string[]
  series: string[]; gs1_owner: string | null; product_count: number
}
type SummaryV2 = {
  brands: number; brands_with_prefixes: number; total_prefixes: number
  total_series: number; branded_products: number; by_tier: Record<string, number>
}

export default function BrandRegistryV2() {
  const [rows, setRows] = useState<BrandV2[]>([])
  const [sum, setSum] = useState<SummaryV2 | null>(null)
  const [err, setErr] = useState('')
  const [q, setQ] = useState('')
  const [open, setOpen] = useState<string | null>(null)

  useEffect(() => {
    api.get<SummaryV2>('/api/brands-v2/summary').then(setSum).catch(() => {})
    api.get<{ brands: BrandV2[] }>('/api/brands-v2')
      .then((d) => setRows(d.brands))
      .catch((e) => setErr(e instanceof ApiError ? e.message : 'Регистърът не се зареди.'))
  }, [])

  const shown = rows.filter((r) =>
    !q || r.canonical.toLowerCase().includes(q.toLowerCase()) ||
    r.aliases.some((a) => a.includes(q.toLowerCase())))

  return (
    <main style={{ padding: '1rem', maxWidth: 1000, margin: '0 auto' }}>
      <h1 style={{ fontSize: '1.2rem' }}>Марки — опростен регистър (v2)</h1>
      {sum && (
        <p className="font-type" style={{ color: 'var(--pencil)', fontSize: '.85rem' }}>
          {sum.brands} марки · {sum.brands_with_prefixes} с префикси · {sum.total_prefixes} префикса ·
          {' '}{sum.total_series} серии · {sum.branded_products} продукта с марка
          {sum.by_tier && ` (баркод ${sum.by_tier.barcode || 0}, име ${sum.by_tier.name || 0}, скрейп ${sum.by_tier.scrape || 0})`}
        </p>
      )}
      <input placeholder="търси марка или изписване…" value={q} onChange={(e) => setQ(e.target.value)}
        style={{ width: '100%', padding: '.5rem', margin: '.4rem 0', borderRadius: 8, border: '1px solid var(--line)' }} />
      {err && <p style={{ color: 'var(--red)' }}>{err}</p>}
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.85rem' }}>
        <thead>
          <tr style={{ textAlign: 'left', borderBottom: '2px solid var(--line)' }}>
            <th style={{ padding: '.4rem' }}>Марка</th><th>Продукти</th>
            <th>Префикси</th><th>Изписвания</th><th>Серии</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((r) => (
            <>
              <tr key={r.id} style={{ borderBottom: '1px solid var(--line)', cursor: 'pointer' }}
                onClick={() => setOpen(open === r.id ? null : r.id)}>
                <td style={{ padding: '.4rem', fontWeight: 700 }}>{r.canonical}</td>
                <td style={{ color: 'var(--red)', fontWeight: 700 }}>{r.product_count}</td>
                <td>{r.prefixes.length}</td>
                <td>{r.aliases.length}</td>
                <td>{r.series.length}</td>
              </tr>
              {open === r.id && (
                <tr><td colSpan={5} style={{ padding: '.4rem .8rem', background: '#fafbfc' }}>
                  <div style={{ fontSize: '.8rem', display: 'grid', gap: '.3rem' }}>
                    {r.gs1_owner && <div><b>GS1 собственик:</b> {r.gs1_owner}</div>}
                    {r.prefixes.length > 0 && <div><b>Префикси:</b> {r.prefixes.join(', ')}</div>}
                    {r.aliases.length > 0 && <div><b>Изписвания:</b> {r.aliases.join(', ')}</div>}
                    {r.series.length > 0 && <div><b>Серии:</b> {r.series.join(', ')}</div>}
                    {r.prefixes.length === 0 &&
                      <div style={{ color: 'var(--pencil)' }}>Няма префикси — разпознава се по име (напр. барекодирано от вносител).</div>}
                  </div>
                </td></tr>
              )}
            </>
          ))}
        </tbody>
      </table>
    </main>
  )
}
