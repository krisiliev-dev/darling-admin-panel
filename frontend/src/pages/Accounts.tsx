import { useCallback, useEffect, useState } from 'react'
import { api } from '../lib/api'
import { PageHeader, Loading } from '../components/PageHeader'

// Сметки — read-only browser over Barsy accounts (Продажби по сметки). Barsy owns the account/sale
// lifecycle; the panel only views them. Backend: /api/accounts (#80). Feeds #72 УНП capture.
type AccountRow = {
  account_id: number
  account_sid?: string | null
  create_date?: string | null
  user_name?: string | null
  pos_name?: string | null
  barsy_name?: string | null
}

const cell = { padding: '.3rem .4rem' }

export function Accounts() {
  const [rows, setRows] = useState<AccountRow[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<number | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    api.get<{ accounts: AccountRow[] }>('/api/accounts')
      .then((r) => setRows(Array.isArray(r.accounts) ? r.accounts : []))
      .catch(() => setRows([]))
      .finally(() => setLoading(false))
  }, [])
  useEffect(() => { void load() }, [load])

  if (selected !== null) {
    return <AccountDetail id={selected} onBack={() => setSelected(null)} />
  }

  return (
    <div style={{ display: 'grid', gap: '1rem' }}>
      <PageHeader title="Сметки" subtitle="продажби по сметки — от Barsy (само за преглед)" />
      {loading ? <Loading /> : rows.length === 0 ? (
        <p className="pencil">Още няма сметки.</p>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '.85rem' }}>
          <thead>
            <tr style={{ textAlign: 'left', color: 'var(--pencil)', fontSize: '.72rem' }}>
              <th style={cell}>№</th>
              <th style={cell}>Дата</th>
              <th style={cell}>Каса</th>
              <th style={cell}>Потребител</th>
              <th style={cell}>SID</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((a) => (
              <tr key={a.account_id} style={{ borderTop: '1px solid var(--line)', cursor: 'pointer' }}
                onClick={() => setSelected(a.account_id)}>
                <td style={{ ...cell, fontWeight: 700 }}>{a.account_id}</td>
                <td style={cell}>{a.create_date || '—'}</td>
                <td style={cell}>{a.pos_name || '—'}</td>
                <td style={cell}>{a.user_name || '—'}</td>
                <td style={{ ...cell, fontFamily: 'monospace', color: 'var(--pencil)' }}>{a.account_sid || '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function AccountDetail({ id, onBack }: { id: number; onBack: () => void }) {
  const [data, setData] = useState<Record<string, unknown> | null>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    setLoading(true)
    api.get<{ account: Record<string, unknown> }>(`/api/accounts/${id}`)
      .then((r) => setData(r.account || {}))
      .catch(() => setData({}))
      .finally(() => setLoading(false))
  }, [id])

  return (
    <div style={{ display: 'grid', gap: '.9rem', maxWidth: '46rem' }}>
      <PageHeader title={`Сметка #${id}`} subtitle="от Barsy (само за преглед)" />
      <div><button className="btn-paper" onClick={onBack}>← Назад</button></div>
      {loading ? <Loading /> : !data || Object.keys(data).length === 0 ? (
        <p className="pencil">Сметката не е намерена.</p>
      ) : (
        <table style={{ borderCollapse: 'collapse', fontSize: '.82rem' }}>
          <tbody>
            {Object.entries(data).map(([k, v]) => (
              <tr key={k} style={{ borderTop: '1px solid var(--line)' }}>
                <td style={{ ...cell, color: 'var(--pencil)', whiteSpace: 'nowrap', verticalAlign: 'top' }}>{k}</td>
                <td style={cell}>{v == null ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
