import { useEffect, useMemo, useState } from 'react'
import { api } from '../lib/api'
import { Sheet } from '../components/Paper'
import { PageHeader, Loading } from '../components/PageHeader'

// Дневник — the audit trail (owner-only). Every mutating action is logged: semantic events
// (intake.approve, brand.add, catalog.bulk_price…) + every mutating HTTP request (http:POST …).
interface LogRow { id: number; ts: string; actor: string; action: string; target: string; detail: string }
const PER = 100

export function Logs({ embedded = false }: { embedded?: boolean } = {}) {
  const [actions, setActions] = useState<string[]>([])
  const [actor, setActor] = useState('')
  const [action, setAction] = useState('')
  const [q, setQ] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [page, setPage] = useState(1)
  const [data, setData] = useState<{ total: number; items: LogRow[] } | null>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => { api.get<{ actions: string[] }>('/api/logs/actions').then((d) => setActions(d.actions)).catch(() => setActions([])) }, [])

  const query = useMemo(() => {
    const p = new URLSearchParams()
    if (actor.trim()) p.set('actor', actor.trim())
    if (action) p.set('action', action)
    if (q.trim()) p.set('q', q.trim())
    if (from) p.set('date_from', from)
    if (to) p.set('date_to', to)
    p.set('per_page', String(PER)); p.set('page', String(page))
    return p.toString()
  }, [actor, action, q, from, to, page])

  useEffect(() => {
    setLoading(true)
    api.get<{ total: number; items: LogRow[] }>(`/api/logs?${query}`)
      .then(setData).catch(() => setData({ total: 0, items: [] })).finally(() => setLoading(false))
  }, [query])
  useEffect(() => { setPage(1) }, [actor, action, q, from, to])

  const items = data?.items || []
  const total = data?.total || 0
  const pages = Math.max(1, Math.ceil(total / PER))
  const fmt = (iso: string) => { try { return new Date(iso).toLocaleString('bg-BG') } catch { return iso } }

  return (
    <div style={{ display: 'grid', gap: '1.1rem' }}>
      {!embedded && <PageHeader title="Дневник" subtitle="одитна следа — кой какво е правил в панела" />}
      <Sheet clip="left">
        <div style={{ display: 'flex', gap: '.5rem', flexWrap: 'wrap', alignItems: 'center', marginBottom: '.6rem' }}>
          <input className="input-paper" style={{ width: '12rem' }} placeholder="потребител (имейл)…" value={actor} onChange={(e) => setActor(e.target.value)} />
          <select className="input-paper" value={action} onChange={(e) => setAction(e.target.value)}>
            <option value="">— действие —</option>
            {actions.map((a) => <option key={a} value={a}>{a}</option>)}
          </select>
          <input className="input-paper" style={{ width: '12rem' }} placeholder="търси в детайли…" value={q} onChange={(e) => setQ(e.target.value)} />
          <span className="font-type" style={{ fontSize: '.72rem', color: 'var(--pencil)' }}>период:</span>
          <input className="input-paper" type="date" value={from} onChange={(e) => setFrom(e.target.value)} title="от дата" />
          <input className="input-paper" type="date" value={to} onChange={(e) => setTo(e.target.value)} title="до дата" />
          {(actor || action || q || from || to) && <button className="btn-paper" style={{ fontSize: '.7rem' }} onClick={() => { setActor(''); setAction(''); setQ(''); setFrom(''); setTo('') }}>изчисти</button>}
          <span className="font-type" style={{ fontSize: '.72rem', color: 'var(--pencil)', marginLeft: 'auto' }}>{data ? `${total} записа` : ''}</span>
        </div>
        {loading && !data ? <Loading /> : items.length === 0 ? (
          <p className="font-type" style={{ color: 'var(--pencil)', fontSize: '.85rem' }}>Няма записи за този филтър.</p>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table className="font-type" style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.78rem' }}>
              <thead><tr style={{ borderBottom: '1px solid var(--line)', color: 'var(--red)', textAlign: 'left' }}>
                <th style={{ padding: '.3rem' }}>Кога</th><th style={{ padding: '.3rem' }}>Потребител</th>
                <th style={{ padding: '.3rem' }}>Действие</th><th style={{ padding: '.3rem' }}>Обект</th>
                <th style={{ padding: '.3rem' }}>Детайли</th>
              </tr></thead>
              <tbody>
                {items.map((r) => (
                  <tr key={r.id} style={{ borderBottom: '1px dashed var(--line)' }}>
                    <td style={{ padding: '.3rem', whiteSpace: 'nowrap', color: 'var(--pencil)' }}>{fmt(r.ts)}</td>
                    <td style={{ padding: '.3rem' }}>{r.actor}</td>
                    <td style={{ padding: '.3rem' }}><span style={{ color: r.action.startsWith('http:') ? 'var(--pencil)' : 'var(--ink)' }}>{r.action}</span></td>
                    <td style={{ padding: '.3rem', color: 'var(--pencil)', maxWidth: '20rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.target}>{r.target || '—'}</td>
                    <td style={{ padding: '.3rem', color: 'var(--pencil)', maxWidth: '22rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.detail}>{r.detail || '—'}</td>
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
    </div>
  )
}
