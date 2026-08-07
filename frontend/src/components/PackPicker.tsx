import { useEffect, useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { DeliveryLineRow } from '../lib/api'

// P1 — receiving a line in packs (разфасовка). The supplier bills "6 каси" and 72 glasses arrive;
// booking 6 is the commonest silent stock drift in home goods.
//
// Two things this component refuses to do, both deliberate:
//   * it never infers a pack from a column heading — a wrong factor multiplies the error by the
//     pack size, so a human taps it;
//   * it shows the arithmetic (6 × 12 = 72 бр) BEFORE the tap, so the number that lands in stock is
//     one somebody actually agreed to.
// The factor itself is resolved server-side from the pack's Barsy id; this component only ever
// sends the id, so a page left open overnight cannot multiply by a stale number.

type Pack = { id: number; name: string; qty: number }

export function PackPicker({ batchId, line, onDone }: { batchId: number; line: DeliveryLineRow; onDone?: () => void }) {
  const [open, setOpen] = useState(false)
  const [packs, setPacks] = useState<Pack[] | null>(null)
  const [packId, setPackId] = useState<number | ''>('')
  const [manual, setManual] = useState('')
  const [count, setCount] = useState('1')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open || packs) return
    api.get<{ packages: Pack[] }>('/api/deliveries/packages')
      .then((d) => setPacks(Array.isArray(d?.packages) ? d.packages : []))
      .catch(() => setPacks([]))
  }, [open, packs])

  const chosen = packs?.find((p) => p.id === packId)
  const factor = chosen ? chosen.qty : Number(manual) || 0
  const n = Number(count) || 0
  const total = n > 0 && factor > 0 ? n * factor : 0

  const apply = async () => {
    setBusy(true); setError('')
    try {
      await api.post(`/api/deliveries/${batchId}/lines/${line.id}/pack`,
        chosen ? { pack_count: n, pack_id: chosen.id } : { pack_count: n, pack_factor: factor })
      setOpen(false)
      onDone?.()
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Неуспешно.') } finally { setBusy(false) }
  }

  if (!open) {
    const has = (line.pack_count ?? 0) > 0 && (line.pack_factor ?? 1) > 1
    return (
      <button
        className="btn-paper"
        style={{ fontSize: '.72rem', padding: '.15rem .4rem', color: has ? 'var(--ink)' : 'var(--pencil)' }}
        title={has ? 'Прието по разфасовки' : 'Приеми по разфасовки (каса, кашон, стек)'}
        onClick={() => setOpen(true)}
      >
        {has ? `${line.pack_count} × ${line.pack_factor}` : 'опак.'}
      </button>
    )
  }

  return (
    <div className="font-type" style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '.3rem', fontSize: '.72rem' }}>
      <input
        type="number" min={0} step="any" value={count}
        onChange={(e) => setCount(e.target.value)}
        style={{ width: '3rem', fontSize: '.72rem', padding: '.1rem .2rem' }}
        aria-label="Брой опаковки"
      />
      <span>×</span>
      <select
        value={packId}
        onChange={(e) => { setPackId(e.target.value === '' ? '' : Number(e.target.value)); setManual('') }}
        style={{ fontSize: '.72rem', padding: '.1rem .2rem' }}
        aria-label="Разфасовка"
      >
        <option value="">— друга —</option>
        {/* keyed by id, never by name: „Стек" exists as both 6 and 24 */}
        {packs?.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.qty})</option>)}
      </select>
      {packId === '' && (
        <input
          type="number" min={0} step="any" value={manual} placeholder="бр/оп."
          onChange={(e) => setManual(e.target.value)}
          style={{ width: '3.5rem', fontSize: '.72rem', padding: '.1rem .2rem' }}
          aria-label="Брой в опаковка"
        />
      )}
      <span style={{ color: total > 0 ? 'var(--ready)' : 'var(--pencil)' }}>
        = {total > 0 ? `${total} бр` : '—'}
      </span>
      <button className="btn-paper btn-red" style={{ fontSize: '.72rem', padding: '.15rem .4rem' }}
        disabled={busy || total <= 0} onClick={() => void apply()}>
        {busy ? '…' : 'Приложи'}
      </button>
      <button className="btn-paper" style={{ fontSize: '.72rem', padding: '.15rem .4rem' }}
        disabled={busy} onClick={() => { setOpen(false); setError('') }}>Отказ</button>
      {error && <span style={{ color: 'var(--red)' }}>{error}</span>}
    </div>
  )
}
