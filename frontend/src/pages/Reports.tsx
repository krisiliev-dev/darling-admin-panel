import { useCallback, useEffect, useState } from 'react'
import { api } from '../lib/api'
import { PageHeader, Loading } from '../components/PageHeader'

// Справки (#84) — read-only browser over Barsy's report pages, proxied by /api/reports. Barsy is
// the system of record; the panel renders whatever columns the report itself declares (BG titles
// from the page structure, empty columns already dropped server-side). v1: no filters — each
// report shows Barsy's default window; filters come once real sales data exists.
type ReportRef = { key: string; title: string }
type Group = { group: string; reports: ReportRef[] }
type Column = { name: string; title: string }
type ReportData = { key: string; title: string; columns: Column[]; rows: Record<string, unknown>[]; total: number | null; page: number; per_page: number }

const cell = { padding: '.3rem .4rem' }
const PER = 100

const show = (v: unknown): string => {
  if (v == null || v === '') return '—'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

export function Reports() {
  const [groups, setGroups] = useState<Group[]>([])
  const [sel, setSel] = useState('')
  const [page, setPage] = useState(1)
  const [data, setData] = useState<ReportData | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    api.get<{ groups: Group[] }>('/api/reports')
      .then((r) => setGroups(Array.isArray(r.groups) ? r.groups : []))
      .catch(() => setGroups([]))
  }, [])

  const load = useCallback((key: string, p: number) => {
    if (!key) { setData(null); return }
    setLoading(true)
    api.get<ReportData>(`/api/reports/${key}?rows=${PER}&page=${p}`)
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false))
  }, [])
  useEffect(() => { load(sel, page) }, [sel, page, load])

  const pick = (key: string) => { setSel(key); setPage(1) }
  const rows = data?.rows || []
  const canNext = rows.length === PER || (data?.total != null && page * PER < data.total)

  return (
    <div style={{ display: 'grid', gap: '1rem' }}>
      <PageHeader title="Справки" subtitle="отчетите на Barsy — на живо, само за преглед" />
      <div style={{ display: 'flex', gap: '.6rem', alignItems: 'center', flexWrap: 'wrap' }}>
        <select className="input-paper" value={sel} onChange={(e) => pick(e.target.value)}>
          <option value="">— избери справка —</option>
          {groups.map((g) => (
            <optgroup key={g.group} label={g.group}>
              {g.reports.map((r) => <option key={r.key} value={r.key}>{r.title}</option>)}
            </optgroup>
          ))}
        </select>
        {data && (
          <span className="font-type" style={{ fontSize: '.74rem', color: 'var(--pencil)' }}>
            {data.total != null ? `${data.total} реда` : ''} · стр. {page}
          </span>
        )}
        {sel && (
          <span style={{ display: 'inline-flex', gap: '.3rem' }}>
            <button className="btn-paper" style={{ fontSize: '.72rem' }} disabled={page <= 1 || loading}
              onClick={() => setPage((p) => Math.max(1, p - 1))}>← предишна</button>
            <button className="btn-paper" style={{ fontSize: '.72rem' }} disabled={!canNext || loading}
              onClick={() => setPage((p) => p + 1)}>следваща →</button>
          </span>
        )}
      </div>
      {!sel ? (
        <p className="pencil">Избери справка от списъка — данните идват направо от Barsy.</p>
      ) : loading && !data ? <Loading /> : !data ? (
        <p className="pencil">Справката не можа да се зареди.</p>
      ) : rows.length === 0 ? (
        <p className="pencil">Няма данни в този период.</p>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.82rem' }}>
            <thead>
              <tr style={{ textAlign: 'left', color: 'var(--pencil)', fontSize: '.7rem' }}>
                {data.columns.map((c) => <th key={c.name} style={cell}>{c.title}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} style={{ borderTop: '1px solid var(--line)' }}>
                  {data.columns.map((c) => <td key={c.name} style={cell}>{show(r[c.name])}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
