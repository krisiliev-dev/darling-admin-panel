import { useState } from 'react'
import { api, ApiError } from '../lib/api'
import type { DeliveryLineRow } from '../lib/api'

// H2/A4 — the way back from a scan. Workers scan a whole box, THEN inspect it, so the tally is
// routinely ahead of reality: mark 2 units defective and the received target drops below what was
// already scanned, leaving a permanently red line. A double-beep does the same. Without this the
// only escape is an owner force-finalise, which defeats the point of scan-confirming at all.

export function ScanAdjust({ batchId, line, onDone }: { batchId: number; line: DeliveryLineRow; onDone?: () => void }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const scanned = line.scanned_qty ?? 0

  const send = async (body: { delta?: number; reset?: boolean }) => {
    setBusy(true); setError('')
    try {
      await api.post(`/api/deliveries/${batchId}/lines/${line.id}/scan-adjust`, body)
      onDone?.()
    } catch (e) { setError(e instanceof ApiError ? e.message : 'Неуспешно.') } finally { setBusy(false) }
  }

  if (scanned <= 0) return <span style={{ color: 'var(--pencil)', fontSize: '.72rem' }}>—</span>

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '.25rem' }}>
      <button
        className="btn-paper" disabled={busy}
        style={{ fontSize: '.72rem', padding: '.15rem .4rem' }}
        title="Едно сканиране назад (двоен звук)"
        aria-label={`Едно сканиране назад за ${line.name || line.supplier_code}`}
        onClick={() => void send({ delta: -1 })}
      >−1</button>
      <button
        className="btn-paper" disabled={busy}
        style={{ fontSize: '.72rem', padding: '.15rem .4rem', color: 'var(--pencil)' }}
        title="Изчисти брояча и преброй наново"
        aria-label={`Изчисти сканиранията за ${line.name || line.supplier_code}`}
        onClick={() => void send({ reset: true })}
      >⟲</button>
      {error && <span style={{ color: 'var(--red)', fontSize: '.7rem' }}>{error}</span>}
    </span>
  )
}
