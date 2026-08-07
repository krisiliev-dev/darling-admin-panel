import { useEffect, useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { DeliveryDetailResp } from '../lib/api'

// The 'От заявка' source of the new-delivery wizard: pick a SENT заявка and start receiving a
// delivery from its lines. Finalising that delivery flips the заявка to „Заредени" (Barsy's
// Ново зареждане carries the same native „Заявка №" link). This is the path for goods we
// ordered ourselves; the file/photo paths are for a supplier's own sheet.

type Open = { id: number; supplier: string; reference: string; created_at: string | null; lines: number }

export function ZayavkaSource({ onCreated }: { onCreated: (id: number) => void }) {
  const [list, setList] = useState<Open[] | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [error, setError] = useState('')

  useEffect(() => {
    api.get<{ zayavki: Open[] }>('/api/deliveries/zayavki-open').then((d) => setList(d.zayavki)).catch(() => setList([]))
  }, [])

  const receive = async (id: number) => {
    setBusyId(id); setError('')
    try {
      const b = await api.post<DeliveryDetailResp>(`/api/deliveries/from-zayavka/${id}`, {})
      onCreated(b.id)
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Създаването се провали.') } finally { setBusyId(null) }
  }

  if (!list) return <span className="font-type" style={{ fontSize: '.8rem', color: 'var(--pencil)' }}>зарежда…</span>
  if (list.length === 0) return <span className="font-type" style={{ fontSize: '.8rem', color: 'var(--pencil)' }}>Няма изпратени заявки за приемане.</span>

  return (
    <div style={{ display: 'grid', gap: '.4rem' }}>
      <span className="font-type" style={{ fontSize: '.74rem', color: 'var(--pencil)' }}>
        Избери изпратена заявка — доставката се създава от нейните редове.
      </span>
      {list.map((z) => (
        <div key={z.id} className="font-type" style={{ display: 'flex', alignItems: 'center', gap: '.6rem', fontSize: '.85rem', border: '1px solid var(--line)', borderRadius: 3, padding: '.4rem .6rem' }}>
          <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis' }}>
            <b>{z.supplier || '—'}</b>
            <span style={{ color: 'var(--pencil)' }}> · {z.lines} реда · {(z.created_at || '').slice(0, 10)}</span>
          </span>
          <button className="btn-paper btn-red" onClick={() => void receive(z.id)} disabled={busyId != null}>
            {busyId === z.id ? 'създава…' : 'Приеми'}
          </button>
        </div>
      ))}
      {error && <div style={{ color: 'var(--red)', fontFamily: 'var(--type)', fontSize: '.82rem' }}>{error}</div>}
    </div>
  )
}
